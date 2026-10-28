#!/usr/bin/env python3
"""
container.py - code a builder wrote runs only in an offline container (orchestrator
isolation, plan section 5, stage S4).

Four parts, standard library only:

  Docker state  ``docker_running``, ``ensure_running`` (start Docker Desktop and wait
                for the engine, saying whether this call started it, so a run stops
                Docker only if it started it) and ``stop_docker``.
  The image     ``requirements_files`` (the root requirements.txt with every file it
                includes, resolved as pip resolves them, plus this workflow's own),
                ``write_constraints`` (``pip freeze --exclude-editable`` from the
                project .venv), ``image_tags`` (the tags pip in the base image
                installs, asked of the image offline), ``fill_wheel_store`` (for each
                pinned release the wheel pip there prefers, ``wheel_rank``, fetched
                from its own PyPI record and checked by its SHA-256, kept outside the
                project and reused), ``image_tag`` (a hash over the
                requirements, the constraints and the Dockerfile), ``build_image``,
                which builds from a generated Dockerfile when the tag is absent,
                installing from the wheels with no index and no network, and then
                compares the image's package versions with the constraints, and
                ``prepare_image``, a run's image step (a build, or a dry run's
                preview).
  The copy      ``start_ignored`` (the ignore floor, recorded once at run start),
                ``make_copy`` (a clone at the start commit with the live tracked and
                unignored files over it, nothing on the floor, no link of any kind)
                and ``remove_copy``.
  Running       ``run_in_container``: no network, no added privileges, memory and
                process limits, the clean-copy marker, a list argv, never a shell.

Every Docker command goes through ``docker()``, so the tests replace that one
function. Every program this module starts, Docker, git and pip alike, is started by
``_start``, which gives it an empty stdin (decision 37): the check helper is a stdio
MCP server, so an inherited stdin would be the MCP pipe, and a child holding it kept
each check from answering until the client closed the pipe. Every process started
here runs on the host, so none is given the clean-copy marker; only the container gets
it, with ``-e``. Built and tested in S4;
the loop calls it since S6b (Docker at a run's start, resume and end, the image step,
and the checks after every builder turn) and the check helper from S7 (decision 28).
"""

import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

BASE_IMAGE = "python:3.13-slim"
IMAGE_REPO = "bookdragon-checks"
REVIEW_REQUIREMENTS = "workflows/review-orchestration/requirements.txt"
CONSTRAINTS_NAME = "constraints.txt"
RUN_TIMEOUT = 1800
START_TIMEOUT = 180
POLL_SECONDS = 2
BUILD_TIMEOUT = 3600
CLEAN_COPY_ENV = "BOOK_DRAGON_CLEAN_COPY"
COPIES_DIR = "book-dragon-copies"

_REQ_INCLUDE = re.compile(r"^\s*(?:-r|--requirement)(?:\s*=\s*|\s+|(?=\S))(\S+)")
_STATUS_RUNNING = re.compile(r"^\s*Status\s+running\b", re.IGNORECASE | re.MULTILINE)
_FREEZE_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)")
_UNSAFE_PATH = re.compile(r"[\s\"']")
_IGNORE_SPECIAL = re.compile(r"([\\*?\[\]!#])")


class ContainerError(Exception):
    """A Docker, image, copy or container step that could not be done."""


def _host_env():
    """The process environment without the clean-copy marker: everything started
    here runs on the host."""
    return {k: v for k, v in os.environ.items() if k.upper() != CLEAN_COPY_ENV}


def _start(argv, **options):
    """Run one program and wait for it: ``subprocess.run`` with an empty stdin, always
    (decision 37). The one process start in this module; every other function starts
    programs through it, and a caller's own ``stdin`` is overridden."""
    options["stdin"] = subprocess.DEVNULL
    return subprocess.run(argv, **options)


def docker(args, timeout=None, raise_timeout=False):
    """Run ``docker <args>`` on the host. The one place a Docker command is run.

    A command past its ``timeout`` is a ``ContainerError`` naming the command and the
    limit (code review R11-1), unless ``raise_timeout``: ``run_in_container`` takes
    the timeout itself, since it must then stop the container by name."""
    try:
        return _start(["docker", *args], capture_output=True, encoding="utf-8",
                      errors="replace", timeout=timeout, env=_host_env())
    except OSError as exc:
        raise ContainerError(f"docker could not be run: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        if raise_timeout:
            raise
        raise ContainerError(f"`docker {' '.join(args[:2])}` did not answer within "
                             f"{timeout} s") from exc


def _git(cwd, *args):
    result = _start(["git", "-C", str(cwd), *args], capture_output=True,
                    encoding="utf-8", errors="surrogateescape", env=_host_env())
    if result.returncode != 0:
        raise ContainerError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------------
# Docker state
# ---------------------------------------------------------------------------
def docker_running():
    """Whether Docker Desktop reports ``Status running``."""
    result = docker(["desktop", "status"], timeout=60)
    return result.returncode == 0 and bool(_STATUS_RUNNING.search(result.stdout))


def ensure_running(sleep=time.sleep, clock=time.monotonic):
    """Start Docker Desktop if it is not running and wait for the engine. Returns
    True if this call started it, False if it was already running.

    A Docker Desktop this call started whose engine then never answers is stopped
    again before the error is raised, and the error says whether that stop worked
    (code review R26-2): the caller learns it started Docker only from a return value
    it never gets, so it could not stop it itself."""
    if docker_running():
        return False
    started = docker(["desktop", "start"], timeout=START_TIMEOUT)
    if started.returncode != 0:
        raise ContainerError("Docker Desktop could not be started: "
                             + ((started.stderr or started.stdout).strip() or
                                f"exit {started.returncode}"))
    deadline = clock() + START_TIMEOUT
    try:
        while True:
            if docker(["info"], timeout=60).returncode == 0:
                return True
            if clock() >= deadline:
                raise ContainerError(f"the Docker engine did not answer within "
                                     f"{START_TIMEOUT} s of starting Docker Desktop")
            sleep(POLL_SECONDS)
    except BaseException as exc:
        try:
            stop_docker()
            outcome = "Docker Desktop, started by this call, was stopped again"
        except ContainerError as stop_exc:
            outcome = (f"Docker Desktop, started by this call, could not be stopped "
                       f"again: {stop_exc}")
        if isinstance(exc, ContainerError):
            raise ContainerError(f"{exc}; {outcome}") from exc
        raise


def stop_docker():
    result = docker(["desktop", "stop"], timeout=START_TIMEOUT)
    if result.returncode != 0:
        raise ContainerError("Docker Desktop could not be stopped: "
                             + ((result.stderr or result.stdout).strip() or
                                f"exit {result.returncode}"))


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------
def _includes(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _REQ_INCLUDE.match(line.split(" #", 1)[0])
        if match:
            yield (path.parent / match.group(1)).resolve()


def requirements_files(root):
    """The requirements files the image installs, in a fixed order: the root
    requirements.txt and every file it includes, depth first, each included path
    resolved relative to the including file as pip resolves it; then this
    workflow's own requirements and its includes. Each once."""
    root = Path(root).resolve()
    order, seen = [], set()

    def visit(path):
        if path in seen:
            return
        if not path.is_file():
            raise ContainerError(f"requirements file not found: {path}")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ContainerError(f"requirements file outside the project: {path}") from exc
        seen.add(path)
        order.append(path)
        for included in _includes(path):
            visit(included)

    visit(root / "requirements.txt")
    visit(root / REVIEW_REQUIREMENTS)
    return order


# Prints, as JSON, every installed package with more than one install record.
_DOUBLE_RECORDS = (
    "import collections, importlib.metadata as m, json\n"
    "seen = collections.defaultdict(list)\n"
    "for d in m.distributions():\n"
    "    seen[d.metadata['Name'].lower().replace('_', '-')].append(d.metadata['Version'])\n"
    "print(json.dumps(sorted(f'{n} ' + ' and '.join(sorted(v)) for n, v in seen.items()"
    " if len(v) > 1)))\n")


def write_constraints(python, dest, run=_start):
    """Write ``pip freeze --exclude-editable`` from ``python`` (the project .venv's)
    to ``dest``. A constraint pins a package only when it is installed, so a
    Windows-only package in the freeze is never installed on Linux.

    Refused, naming them, when any package has two install records: ``pip freeze``
    then reports one record's version while the code installed may be the other's,
    so the constraints would not say what is installed (S4 live check, 2026-10-07:
    19 such packages in .venv made the image build impossible)."""
    probe = run([str(python), "-c", _DOUBLE_RECORDS], capture_output=True,
                encoding="utf-8", errors="replace", env=_host_env())
    try:
        doubled = json.loads(probe.stdout) if probe.returncode == 0 else None
    except ValueError:
        doubled = None
    if not isinstance(doubled, list):
        raise ContainerError("could not list the installed packages: "
                             + ((probe.stderr or "").strip() or f"exit {probe.returncode}"))
    if doubled:
        raise ContainerError("the project .venv has packages with two install records, "
                             "so pip freeze cannot say what is installed: "
                             + "; ".join(doubled)
                             + ". Repair .venv (keep the record its files match) and retry.")
    result = run([str(python), "-m", "pip", "freeze", "--exclude-editable"],
                 capture_output=True, encoding="utf-8", errors="replace",
                 env=_host_env())
    if result.returncode != 0 or not result.stdout.strip():
        raise ContainerError("pip freeze failed: "
                             + ((result.stderr or "").strip() or f"exit {result.returncode}"))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(result.stdout.replace("\r\n", "\n"))
    return dest


# ---------------------------------------------------------------------------
# The wheel store (decision 41, replacing decision 26)
# ---------------------------------------------------------------------------
# The image installs its packages from a folder of wheels with its network off, and
# the folder is filled on the host from each pinned release's own PyPI record, never
# from PyPI's index pages. On the build machine an HTTPS scanner breaks one index
# page (`/simple/mcp/`) every time while every release record and file comes through,
# so a build that asks the index fails and one that asks for the pinned files does
# not, and the image needs none of the host's certificates.
WHEEL_STORE_DIR = "book-dragon-wheels"
WHEELS_NAME = "wheels"
PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_FILES = "https://files.pythonhosted.org/"
FETCH_ATTEMPTS = 3
FETCH_TIMEOUT = 120
_SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_TAG_LINE = re.compile(r"[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+")
# Run by Python in BASE_IMAGE, offline: every tag pip there installs, most preferred
# first, one per line. ``packaging`` is the copy pip carries, so the answer is pip's
# own (code review R50-1: three hand-written versions of it were each found wrong).
# The list is framed by its count and an end line, so an answer cut short anywhere is
# refused rather than read as a shorter list (code review R51-3).
_IMAGE_TAGS = ("from pip._vendor.packaging.tags import sys_tags\n"
               "tags = [str(t) for t in sys_tags()]\n"
               "print('COUNT', len(tags))\n"
               "print('\\n'.join(tags))\n"
               "print('END')\n")
_BUILD_TAG = re.compile(r"\d+[A-Za-z0-9_.]*")
# A plain project-name component: letters and digits joined by single `_` or `.`, so a
# doubled or trailing separator, which pip refuses (`__` among them), is never used
# (code review R52-1).
_PLAIN_NAME = re.compile(r"[A-Za-z0-9]+(?:[._][A-Za-z0-9]+)*")


def wheel_store():
    """The store's folder, outside the project like the clean copies. Emptied by
    anything that empties the temporary folder, which costs a fresh download only."""
    return Path(tempfile.gettempdir()) / WHEEL_STORE_DIR


def image_tags():
    """The tags pip in BASE_IMAGE installs, most preferred first, asked of the image
    itself with one ``docker run`` that has no network. Nothing about the image's
    Python, platform or glibc is assumed here, so a new base image needs no change.
    Refused unless the answer is whole, its count line matching the tags between it
    and the end line (R51-3), and is a list of well-formed tags that includes
    ``py3-none-any``, which every Python 3 installs."""
    result = docker(["run", "--rm", "--network", "none", BASE_IMAGE, "python", "-c",
                     _IMAGE_TAGS], timeout=300)
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    count = re.fullmatch(r"COUNT (\d+)", lines[0]) if lines else None
    tags = lines[1:-1]
    if (result.returncode != 0 or not count or lines[-1] != "END"
            or int(count.group(1)) != len(tags) or "py3-none-any" not in tags
            or not all(_TAG_LINE.fullmatch(tag) for tag in tags)):
        raise ContainerError(f"pip in {BASE_IMAGE} did not list the tags it installs: "
                             + ((result.stderr or "").strip()[-600:]
                                or f"exit {result.returncode}, {len(lines)} line(s), "
                                   "not a whole framed list"))
    return tags


def _normal(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def _names_release(filename, name, version):
    """Whether a wheel's name is plainly this release's: the project's name (as wheel
    names spell it), exactly the pinned version, and a build tag, if any, of the
    plain shape pip reads. Anything else is not used rather than interpreted (code
    review R51-1: a name pip refuses must never be chosen)."""
    parts = filename[:-4].split("-") if filename.endswith(".whl") else []
    if len(parts) not in (5, 6):
        return False
    return (bool(_PLAIN_NAME.fullmatch(parts[0]))
            and _normal(parts[0]) == _normal(name) and parts[1] == version
            and (len(parts) == 5 or bool(_BUILD_TAG.fullmatch(parts[2]))))


def wheel_rank(filename, ranks):
    """Where this wheel stands in the image's preference, or None if pip there would
    not install it: the best rank of any tag its compressed tag sets expand to. Each
    tag is looked up as pip reads a wheel name (``packaging.utils.
    parse_wheel_filename``): lowercased, otherwise as written, so ``manylinux_2_05``
    is not ``manylinux_2_5``. ``ranks`` maps a tag to its position in
    ``image_tags()``."""
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6):
        return None
    pythons, abis, platforms = (part.lower().split(".") for part in parts[-3:])
    found = [ranks[f"{p}-{a}-{pl}"] for p in pythons for a in abis for pl in platforms
             if f"{p}-{a}-{pl}" in ranks]
    return min(found) if found else None


def _pins(constraints):
    """The ``name==version`` lines of a constraints file, in file order."""
    pins = []
    for line in Path(constraints).read_text(encoding="utf-8").splitlines():
        match = _FREEZE_LINE.match(line)
        if match:
            pins.append((match.group(1), match.group(2)))
    return pins


def _get(url):
    """One HTTPS GET with the host's own certificate store, so an HTTPS scanner the
    host trusts is trusted here and every certificate is still verified."""
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response:
        return response.read()


def _fetch(get, url, what):
    """``get(url)``, tried FETCH_ATTEMPTS times; the last failure is a
    ``ContainerError`` naming what was being fetched and from where."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return get(url)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if attempt == FETCH_ATTEMPTS:
                raise ContainerError(f"{what} could not be downloaded from {url} "
                                     f"({FETCH_ATTEMPTS} tries): {exc}") from exc
    raise AssertionError("unreachable")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _checked_file(entry, release):
    """A release record's file entry, refused unless its name is a plain file name,
    its address is PyPI's file host and it carries a SHA-256."""
    name, url = entry.get("filename"), entry.get("url")
    sha = (entry.get("digests") or {}).get("sha256")
    if not (isinstance(name, str) and _SAFE_FILE.fullmatch(name)
            and isinstance(url, str) and url.startswith(PYPI_FILES)
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise ContainerError(f"the PyPI record for {release} lists a file this build "
                             f"will not take: {entry.get('filename')!r}")
    return name, url, sha


def _stored(folder, entry, release, get):
    """The file a release record names, in ``folder``: reused when the copy there
    has the record's SHA-256, else downloaded and checked before it is kept."""
    name, url, sha = _checked_file(entry, release)
    target = folder / name
    if target.is_file() and _sha256(target) == sha:
        return target
    data = _fetch(get, url, f"{release} ({name})")
    if hashlib.sha256(data).hexdigest() != sha:
        raise ContainerError(f"{name} for {release} does not match the SHA-256 PyPI "
                             "publishes for it; nothing was kept")
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".{name}.{uuid.uuid4().hex}.part"
    partial.write_bytes(data)
    os.replace(partial, target)
    return target


BUILT_RECORD = "built.json"


def _reusable_build(folder, source_sha, ranks):
    """The wheel a previous build kept in ``folder``, or None. Reused only while its
    record names the source archive the release record still lists (by SHA-256)
    and the wheel still has the SHA-256 recorded when it was built (code review
    R48-1): a built wheel has no PyPI digest of its own, so its record is what
    proves it is the wheel that source produced. It must also still be one the
    image installs."""
    try:
        record = json.loads((folder / BUILT_RECORD).read_text(encoding="utf-8"))
        wheel = folder / record["wheel"]
        if (record["source_sha256"] == source_sha and _SAFE_FILE.fullmatch(record["wheel"])
                and wheel_rank(record["wheel"], ranks) is not None and wheel.is_file()
                and _sha256(wheel) == record["wheel_sha256"]):
            return wheel
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _built(store, release, entry, python, ranks, get, run):
    """A release PyPI publishes only as source: its source file is fetched and
    checked like a wheel, then built into a wheel by the host's Python with the
    project .venv's own setuptools, nothing fetched for the build. A built wheel
    is kept with a record of both SHA-256s and reused while both still hold; one
    the image could not install is refused."""
    folder = store / "built" / re.sub(r"[^A-Za-z0-9.]+", "_", release)
    _name, _url, source_sha = _checked_file(entry, release)
    reused = _reusable_build(folder, source_sha, ranks)
    if reused:
        return reused
    kept = sorted(folder.glob("*.whl")) if folder.is_dir() else []
    source = _stored(store / "source", entry, release, get)
    with tempfile.TemporaryDirectory(prefix="wheel-build-") as out:
        result = run([str(python), "-m", "pip", "wheel", "--no-deps",
                      "--no-build-isolation", "--no-index", "-w", out, str(source)],
                     capture_output=True, encoding="utf-8", errors="replace",
                     env=_host_env())
        wheels = sorted(Path(out).glob("*.whl"))
        if result.returncode != 0 or len(wheels) != 1:
            raise ContainerError(f"{release} is published only as source and its wheel "
                                 "could not be built: "
                                 + ((result.stderr or result.stdout).strip()[-800:] or
                                    f"exit {result.returncode}"))
        if wheel_rank(wheels[0].name, ranks) is None:
            raise ContainerError(f"{release} is published only as source and builds "
                                 f"{wheels[0].name}, which the image cannot install")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / BUILT_RECORD).unlink(missing_ok=True)
        for old in kept:
            old.unlink()
        target = folder / wheels[0].name
        shutil.copyfile(wheels[0], target)
        record = {"source_sha256": source_sha, "wheel": target.name,
                  "wheel_sha256": _sha256(target)}
        with open(folder / BUILT_RECORD, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle)
    return target


def fill_wheel_store(constraints, store, python, tags, get=_get, run=_start):
    """For every pinned release, the wheel the image installs, in ``store``.
    ``tags`` is ``image_tags()``: of a release's wheels the image installs, the one
    whose best tag pip there prefers most is taken, as pip would choose. Returns
    ``(wheels, skipped)``: one path per release that has one, and the releases with
    neither a wheel the image can install nor a source file (Windows-only ones such
    as pywin32), which the image does not need. A release the image does need and
    the store lacks fails the offline install, naming it."""
    store = Path(store)
    ranks = {}
    for position, tag in enumerate(tags):
        ranks.setdefault(tag, position)
    wheels, skipped = [], []
    for name, version in _pins(constraints):
        release = f"{name}=={version}"
        url = PYPI_RELEASE.format(name=name, version=version)
        try:
            record = json.loads(_fetch(get, url, f"the PyPI record for {release}"))
            files = record["urls"]
            if not isinstance(files, list):
                raise TypeError("urls is not a list")
        except (ValueError, KeyError, TypeError) as exc:
            raise ContainerError(f"the PyPI record for {release} could not be "
                                 f"read: {exc}") from exc
        # A file name that is not a plain file name is refused outright, whatever else
        # is listed, before any filtering could pass it over quietly.
        for entry in files:
            if isinstance(entry, dict) and not (isinstance(entry.get("filename"), str)
                                                and _SAFE_FILE.fullmatch(entry["filename"])):
                raise ContainerError(f"the PyPI record for {release} lists a file this "
                                     f"build will not take: {entry.get('filename')!r}")
        # A yanked file (withdrawn by its publisher) is never used, and a wheel is used
        # only when its name is plainly this release's (code review R51-1, R51-2).
        current = [f for f in files if isinstance(f, dict) and f.get("yanked") is not True]
        usable = sorted(((wheel_rank(str(f.get("filename")), ranks), str(f.get("filename")), f)
                         for f in current if f.get("packagetype") == "bdist_wheel"
                         and _names_release(str(f.get("filename")), name, version)
                         and wheel_rank(str(f.get("filename")), ranks) is not None),
                        key=lambda item: item[:2])
        sources = [f for f in current if f.get("packagetype") == "sdist"]
        if len(usable) > 1 and usable[0][0] == usable[1][0]:
            tied = [item[1] for item in usable if item[0] == usable[0][0]]
            raise ContainerError(f"{release} has {len(tied)} wheels the image would rank "
                                 f"equally ({', '.join(tied)}); pip would choose between "
                                 "them by rules this build does not model, so none is "
                                 "chosen. Pin a release with one, or choose by hand.")
        if usable:
            wheels.append(_stored(store, usable[0][2], release, get))
        elif sources:
            wheels.append(_built(store, release, sources[0], python, ranks, get, run))
        else:
            skipped.append(release)
    return wheels, skipped


def image_tag(root, files, constraints):
    """``bookdragon-checks:<12 hex>``: SHA-256 over each requirements file (its
    project-relative path and bytes, in the given order), then the constraints,
    then the Dockerfile those files give. Nothing about the host is in it, so a
    change to the host's certificates no longer rebuilds the image (decision 41)."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    relative = []
    for path in files:
        rel = Path(path).resolve().relative_to(root).as_posix()
        relative.append(rel)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
        digest.update(b"\0")
    digest.update(Path(constraints).read_bytes())
    digest.update(b"\0")
    digest.update(dockerfile(relative).encode("utf-8"))
    return f"{IMAGE_REPO}:{digest.hexdigest()[:12]}"


def dockerfile(relative_files):
    """The packages come only from the wheel store copied in with the requirements,
    installed with no index and the build step's network off, so nothing in the
    install talks to PyPI (decision 41). Debian's own package source is plain HTTP
    and verified by its signatures, so the git install needs no host certificate."""
    installs = " ".join(f"-r {rel}" for rel in relative_files)
    return (f"FROM {BASE_IMAGE}\n"
            "ENV PIP_DISABLE_PIP_VERSION_CHECK=1\n"
            "COPY . /req\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends git "
            "&& rm -rf /var/lib/apt/lists/*\n"
            "WORKDIR /req\n"
            f"RUN --network=none pip install --no-cache-dir --no-index "
            f"--find-links /req/{WHEELS_NAME} {installs} -c {CONSTRAINTS_NAME}\n"
            "RUN useradd -m checker && git config --system safe.directory '*'\n"
            "USER checker\n"
            "WORKDIR /work\n")


def _versions(freeze_text):
    """{normalised name: version} from ``pip freeze`` output; lines that pin no
    version (editable or URL installs) are left out."""
    out = {}
    for line in freeze_text.splitlines():
        match = _FREEZE_LINE.match(line)
        if match:
            out[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return out


def image_exists(tag):
    return docker(["image", "inspect", tag], timeout=60).returncode == 0


def build_image(root, constraints, context_dir, wheels):
    """Build the checks image unless its tag exists. Returns (tag, built). ``wheels``
    is called only for a build and returns ``(paths, skipped)`` as
    ``fill_wheel_store`` does, so an existing image costs no download. The build
    context holds only the Dockerfile, the requirements files, the constraints file
    and those wheels. After a build, every package both the image and the
    constraints name must have the same version, or the image is removed and the
    build fails."""
    root = Path(root).resolve()
    files = requirements_files(root)
    tag = image_tag(root, files, constraints)
    if image_exists(tag):
        return tag, False
    context = Path(context_dir)
    if context.exists():
        raise ContainerError(f"build context already exists: {context}")
    paths, _skipped = wheels()
    relative = [path.relative_to(root).as_posix() for path in files]
    for rel, path in zip(relative, files):
        (context / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, context / rel)
    shutil.copyfile(constraints, context / CONSTRAINTS_NAME)
    (context / WHEELS_NAME).mkdir(parents=True, exist_ok=True)
    for path in paths:
        shutil.copyfile(path, context / WHEELS_NAME / Path(path).name)
    with open(context / "Dockerfile", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dockerfile(relative))
    # Built and checked under a staging tag; only a passing check gives it the real
    # tag, which is the only one a run looks for, so an image that failed its check
    # is never reused, whether or not its removal worked (code review R13-1).
    staging = f"{tag}-unverified"
    built = docker(["build", "-t", staging, str(context)], timeout=BUILD_TIMEOUT)
    if built.returncode != 0:
        raise ContainerError("image build failed: "
                             + ((built.stderr or built.stdout).strip()[-1500:] or
                                f"exit {built.returncode}"))
    # Every way the version check can fail leads to the removal, a pip freeze that
    # does not answer included (code review R12-2).
    try:
        frozen = docker(["run", "--rm", "--network", "none", staging, "pip", "freeze"],
                        timeout=300)
        code, listed, reason = frozen.returncode, frozen.stdout, None
    except ContainerError as exc:
        code, listed, reason = None, "", str(exc)
    wanted = _versions(Path(constraints).read_text(encoding="utf-8"))
    got = _versions(listed if code == 0 else "")
    mismatched = sorted(f"{name} {wanted[name]} != {got[name]}"
                        for name in wanted.keys() & got.keys() if wanted[name] != got[name])
    if reason or code != 0 or not got or mismatched:
        _remove_unverified(staging, reason or ("; ".join(mismatched) if mismatched else
                                               "pip freeze in the image " +
                                               (f"exited {code}" if code else
                                                "listed nothing")))
    tagged = docker(["tag", staging, tag], timeout=60)
    if tagged.returncode != 0:
        raise ContainerError(f"the checked image could not be tagged {tag}: "
                             + ((tagged.stderr or "").strip() or f"exit {tagged.returncode}"))
    # Only the staging name is dropped here; the image keeps its real tag. A staging
    # name left behind is harmless, since nothing looks it up.
    docker(["image", "rm", staging], timeout=60)
    return tag, True


UNKNOWN_IMAGE = "unknown: Docker is not running"


def prepare_image(root, python, work_dir, *, dry_run=False):
    """The run's image step (plan 7.1 item 4). Writes the constraints file into
    ``work_dir`` (a temporary folder: decision 32), then for a real start builds the
    image unless its tag exists and returns ``{"tag", "built", "constraints"}``. A dry
    run builds nothing and returns ``{"tag", "would"}``, ``would`` being "reuse",
    "build", or ``UNKNOWN_IMAGE`` while Docker is not running, since only the engine
    can say whether the tag exists and a dry run never starts it (R24-1)."""
    work_dir = Path(work_dir)
    constraints = write_constraints(python, work_dir / CONSTRAINTS_NAME)
    if dry_run:
        files = requirements_files(root)
        tag = image_tag(root, files, constraints)
        if not docker_running():
            return {"tag": tag, "would": UNKNOWN_IMAGE}
        return {"tag": tag, "would": "reuse" if image_exists(tag) else "build"}
    tag, built = build_image(root, constraints, work_dir / "context",
                             lambda: fill_wheel_store(constraints, wheel_store(), python,
                                                      image_tags()))
    return {"tag": tag, "built": built, "constraints": constraints}


def _remove_unverified(staging, reason):
    """Remove an image whose version check did not pass, and raise saying whether
    the removal worked (code review R12-2). It never had the real tag, so it is
    never reused either way (R13-1); a failed removal only leaves space taken."""
    try:
        removed = docker(["image", "rm", "-f", staging], timeout=300).returncode == 0
    except ContainerError:
        removed = False
    outcome = ("the image was removed" if removed else
               f"the unchecked image could not be removed; it is never reused, but it "
               f"takes space (docker image rm -f {staging})")
    raise ContainerError(f"image build failed its version check: {reason}; {outcome}")


# ---------------------------------------------------------------------------
# The clean copy
# ---------------------------------------------------------------------------
_ENV_NAME = re.compile(r"^\.env(\..+)?$", re.IGNORECASE)


def start_ignored(root):
    """The ignore floor, recorded once at run start: every path git ignores (an
    ignored folder as one entry ending ``/``), plus every file named ``.env`` or
    ``.env.<anything>`` that git does not track, wherever it is. Sorted."""
    root = Path(root).resolve()
    ignored = {p for p in _git(root, "ls-files", "-o", "-i", "--exclude-standard",
                               "--directory", "-z").split("\0") if p}
    tracked = {p for p in _git(root, "ls-files", "-z").split("\0") if p}
    ignored_dirs = {p.rstrip("/") for p in ignored if p.endswith("/")}
    for folder, dirs, files in os.walk(root):
        rel_folder = Path(folder).relative_to(root).as_posix()
        rel_folder = "" if rel_folder == "." else rel_folder + "/"
        dirs[:] = [d for d in dirs if d != ".git" and f"{rel_folder}{d}" not in ignored_dirs]
        for name in files:
            rel = f"{rel_folder}{name}"
            if _ENV_NAME.match(name) and rel not in tracked:
                ignored.add(rel)
    for path in ignored:
        if "\n" in path or "\r" in path:
            raise ContainerError(f"cannot record an ignored path holding a line break: {path!r}")
    return sorted(ignored)


def write_start_ignored(paths, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{p}\n" for p in paths))
    return dest


def read_start_ignored(path):
    return [line for line in Path(path).read_text(encoding="utf-8").split("\n") if line]


def _on_floor(rel, floor_files, floor_dirs):
    if rel in floor_files:
        return True
    parts = rel.split("/")
    return any("/".join(parts[:i]) in floor_dirs for i in range(1, len(parts)))


def _exclude_line(entry):
    """An entry of the floor as an anchored ``.git/info/exclude`` pattern, with
    every character gitignore treats specially escaped."""
    body = entry.rstrip("/")
    escaped = _IGNORE_SPECIAL.sub(r"\\\1", body)
    if escaped != escaped.rstrip(" "):
        escaped = escaped.rstrip(" ") + "\\ " * (len(escaped) - len(escaped.rstrip(" ")))
    return "/" + escaped + ("/" if entry.endswith("/") else "")


def _refuse_links(root, paths, gitlinks):
    """Refuse, naming the path, a listed path that is a gitlink, a symbolic link or
    a junction, or that lies under one."""
    checked = {}
    for rel in paths:
        if rel in gitlinks:
            raise ContainerError(f"refusing to copy a gitlink (submodule): {rel}")
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in checked:
                full = Path(root, *parts[:i])
                checked[sub] = os.path.islink(full) or os.path.isjunction(full)
            if checked[sub]:
                raise ContainerError(f"refusing to copy a link or junction: {sub}")


def _refuse_link_in_copy(dest, rel):
    """Before a file is written into the copy: refuse if its path there, or any
    folder above it inside the copy, is a link or junction, which a write would
    follow out of the copy (code review R12-3, the second layer)."""
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        full = Path(dest, *parts[:i])
        if os.path.islink(full) or os.path.isjunction(full):
            raise ContainerError("refusing to write through a link in the copy: "
                                 + "/".join(parts[:i]))


# Decision 30: on this machine git can be refused permission to write one of its own
# object files in a new repository in the temp folder (most likely an antivirus scan
# holding a just-written file). That one failure is retried; every other fails at once.
OBJECT_WRITE_REFUSED = re.compile(r"unable to write file .*objects.*Permission denied",
                                  re.IGNORECASE | re.DOTALL)
CLONE_ATTEMPTS = 3
CLONE_PAUSE = 0.5


def _clone(root, dest, run=_start, sleep=time.sleep):
    """``git clone`` of ``root`` into ``dest``, retried while git is refused permission
    to write one of its own object files (decision 30). A failed attempt's partial
    clone is removed before the next. Returns the last attempt's result."""
    argv = ["git", "clone", "--quiet", "--no-hardlinks", "--config", "core.autocrlf=false",
            "--config", "core.symlinks=false", str(root), str(dest)]
    for attempt in range(1, CLONE_ATTEMPTS + 1):
        result = run(argv, capture_output=True, encoding="utf-8", errors="replace",
                     env=_host_env())
        if (result.returncode == 0 or attempt == CLONE_ATTEMPTS
                or not OBJECT_WRITE_REFUSED.search(result.stderr or "")):
            return result
        remove_copy(dest)
        sleep(CLONE_PAUSE)
    return result


def make_copy(root, dest, public_head, floor):
    """A clean copy of the project for the container, at ``dest``: ``git clone`` at
    ``public_head``, then the live tree's tracked and unignored files over it and
    every tracked file the live tree no longer has removed. Left out: everything git
    ignores now and everything on ``floor`` (the start-of-run ignore list) or under a
    folder on it; the floor is also written to the copy's ``.git/info/exclude``, so
    ``git check-ignore`` in the copy still reports it. A link, junction or gitlink is
    refused before anything is copied. Returns ``dest``."""
    root = Path(root).resolve()
    dest = Path(dest)
    floor_files = {p for p in floor if not p.endswith("/")}
    floor_dirs = {p.rstrip("/") for p in floor if p.endswith("/")}
    listed = [p for p in _git(root, "ls-files", "-c", "-o", "--exclude-standard",
                              "-z").split("\0") if p]
    wanted = [p for p in listed if not _on_floor(p, floor_files, floor_dirs)]
    gitlinks = {line.split("\t", 1)[1] for line in
                _git(root, "ls-files", "-s", "-z").split("\0")
                if line.startswith("160000 ")}
    _refuse_links(root, wanted, gitlinks)
    if dest.exists():
        raise ContainerError(f"copy destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # core.autocrlf=false: files are checked out exactly as committed, whatever the
    # host's git configuration, since the copy is read on Linux; without it a
    # Windows host's autocrlf=true makes every unchanged file look modified.
    # core.symlinks=false: a link in the start commit is checked out as a plain file
    # holding its target's path, so nothing in the copy can point anywhere (code
    # review R12-3); a file written over it stays inside the copy.
    clone = _clone(root, dest)
    if clone.returncode != 0:
        raise ContainerError(f"git clone failed: {clone.stderr.strip()}")
    _git(dest, "checkout", "--quiet", public_head)
    present = set()
    for rel in wanted:
        src = root / rel
        if not src.is_file():  # tracked but deleted in the live tree
            continue
        target = dest / rel
        _refuse_link_in_copy(dest, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target, follow_symlinks=False)
        present.add(rel)
    for rel in (p for p in _git(dest, "ls-files", "-z").split("\0") if p):
        if rel not in present:
            (dest / rel).unlink(missing_ok=True)
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n# Ignored at the start of the run (the ignore floor)\n")
        handle.write("".join(_exclude_line(p) + "\n" for p in floor))
    return dest


def copy_dir(run_id, n):
    """Where copy ``n`` of a run goes: under the system temp folder, never inside the
    run's frozen folder."""
    return Path(tempfile.gettempdir()) / COPIES_DIR / run_id / f"copy-{n}"


def next_copy(run_id):
    """The next unused copy folder for the run: one past the highest number there. The
    loop's checks after a builder turn and the check helper share it, so their copies
    never collide."""
    parent = copy_dir(run_id, 0).parent
    numbers = [int(path.name[5:]) for path in parent.glob("copy-*")
               if path.name[5:].isdigit()] if parent.is_dir() else []
    return copy_dir(run_id, max(numbers, default=0) + 1)


def remove_copies(run_id):
    """Remove the run's whole copies folder, ``book-dragon-copies/<run-id>/`` (plan
    7.3, at a stop reason a resume cannot continue)."""
    remove_copy(copy_dir(run_id, 0).parent)


def remove_copy(dest):
    """Remove a copy, read-only git objects included."""
    def writable(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if Path(dest).exists():
        shutil.rmtree(dest, onexc=writable)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def docker_path(copy):
    """The copy's path as Docker takes it, forward slashes; a path holding a space
    or a quote is refused, as the hook command is."""
    text = Path(copy).as_posix()
    if _UNSAFE_PATH.search(text):
        raise ContainerError(f"the copy path holds a space or a quote: {text}")
    return text


# Docker's own failure inside ``docker run`` (it cannot reach the daemon, cannot find the
# image) exits 125 and reports on stderr in lines that start "docker:"; a check that
# merely exits 125 does not write them (code review R26-1).
DOCKER_RUN_FAILED = 125
_DOCKER_OWN_ERROR = re.compile(r"^docker: ", re.MULTILINE)


def docker_failed(code, err):
    """Whether a ``run_in_container`` result is Docker failing to run the command,
    rather than the command's own exit: exit 125 with Docker's own error line."""
    return code == DOCKER_RUN_FAILED and bool(_DOCKER_OWN_ERROR.search(err or ""))


def run_in_container(copy, argv, image, timeout=RUN_TIMEOUT):
    """Run ``argv`` (a list, never a shell string) in the image over ``copy``,
    offline. Returns (exit code, stdout, stderr). A run past ``timeout`` is
    killed and raises ``ContainerError``."""
    if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
        raise ContainerError("the container command must be a non-empty list of strings")
    name = f"bookdragon-run-{uuid.uuid4().hex[:12]}"
    command = ["run", "--rm", "--name", name, "--network", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--memory", "4g",
               "--pids-limit", "512", "-e", f"{CLEAN_COPY_ENV}=1",
               "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{docker_path(copy)}:/work",
               image, *argv]
    try:
        result = docker(command, timeout=timeout, raise_timeout=True)
    except subprocess.TimeoutExpired as exc:
        # Stopped only if Docker says so (code review R11-2): a kill that fails or
        # does not answer leaves a container that may still be running, named here.
        try:
            killed = docker(["kill", name], timeout=60).returncode == 0
        except ContainerError:
            killed = False
        if killed:
            raise ContainerError(f"the container command ran past {timeout} s and was "
                                 "stopped") from exc
        raise ContainerError(f"the container command ran past {timeout} s and could "
                             f"not be stopped; container {name} may still be running "
                             f"(stop it with: docker kill {name})") from exc
    return result.returncode, result.stdout, result.stderr
