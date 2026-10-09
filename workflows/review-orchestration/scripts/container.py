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
                project .venv), ``image_tag`` (a hash over both) and ``build_image``,
                which builds from a generated Dockerfile when the tag is absent and
                then compares the image's package versions with the constraints.
  The copy      ``start_ignored`` (the ignore floor, recorded once at run start),
                ``make_copy`` (a clone at the start commit with the live tracked and
                unignored files over it, nothing on the floor, no link of any kind)
                and ``remove_copy``.
  Running       ``run_in_container``: no network, no added privileges, memory and
                process limits, the clean-copy marker, a list argv, never a shell.

Every Docker command goes through ``docker()``, so the tests replace that one
function. Every process started here runs on the host, so none is given the
clean-copy marker; only the container gets it, with ``-e``. Built and tested in S4;
the loop starts calling it in S6b and the check helper in S7 (the user's decision A,
2026-10-07; decision 28).
"""

import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import tempfile
import time
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


def docker(args, timeout=None, raise_timeout=False):
    """Run ``docker <args>`` on the host. The one place a Docker command is run.

    A command past its ``timeout`` is a ``ContainerError`` naming the command and the
    limit (code review R11-1), unless ``raise_timeout``: ``run_in_container`` takes
    the timeout itself, since it must then stop the container by name."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=_host_env())
    except OSError as exc:
        raise ContainerError(f"docker could not be run: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        if raise_timeout:
            raise
        raise ContainerError(f"`docker {' '.join(args[:2])}` did not answer within "
                             f"{timeout} s") from exc


def _git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
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
    True if this call started it, False if it was already running."""
    if docker_running():
        return False
    started = docker(["desktop", "start"], timeout=START_TIMEOUT)
    if started.returncode != 0:
        raise ContainerError("Docker Desktop could not be started: "
                             + ((started.stderr or started.stdout).strip() or
                                f"exit {started.returncode}"))
    deadline = clock() + START_TIMEOUT
    while True:
        if docker(["info"], timeout=60).returncode == 0:
            return True
        if clock() >= deadline:
            raise ContainerError(f"the Docker engine did not answer within "
                                 f"{START_TIMEOUT} s of starting Docker Desktop")
        sleep(POLL_SECONDS)


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


def write_constraints(python, dest, run=subprocess.run):
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


TRUST_NAME = "host-roots.crt"
SERVER_AUTH = "1.3.6.1.5.5.7.3.1"  # the TLS server authentication purpose
IMAGE_TRUST_STORE = "/etc/ssl/certs/ca-certificates.crt"


def host_trust_bundle():
    """The root certificates this machine trusts, as one PEM text, sorted and
    without duplicates so the same trust always gives the same bytes. On Windows,
    the system ROOT store; elsewhere, Python's default certificate file. An HTTPS
    scanner on the host (antivirus re-signing every site with its own root, as on
    the build machine) is trusted there, so the image build trusts it too."""
    pems = set()
    if hasattr(ssl, "enum_certificates"):
        for der, encoding, trust in ssl.enum_certificates("ROOT"):
            # Only a root Windows trusts for TLS servers: for every purpose (True),
            # or with server authentication among its purposes (code review R14-1).
            if encoding == "x509_asn" and (trust is True or SERVER_AUTH in trust):
                pems.add(ssl.DER_cert_to_PEM_cert(der).replace("\r\n", "\n"))
    else:
        cafile = ssl.get_default_verify_paths().cafile
        if cafile and Path(cafile).is_file():
            text = Path(cafile).read_text(encoding="utf-8", errors="replace")
            pems.update(re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n?",
                                   text, re.DOTALL))
    return "".join(p if p.endswith("\n") else p + "\n" for p in sorted(pems))


def image_tag(root, files, constraints, trust=""):
    """``bookdragon-checks:<12 hex>``: SHA-256 over each requirements file (its
    project-relative path and bytes, in the given order), then the constraints,
    then the trusted root certificates the build adds."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in files:
        digest.update(Path(path).resolve().relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
        digest.update(b"\0")
    digest.update(Path(constraints).read_bytes())
    digest.update(b"\0")
    digest.update(trust.encode("utf-8"))
    return f"{IMAGE_REPO}:{digest.hexdigest()[:12]}"


def dockerfile(relative_files):
    """The host's trusted roots are added to the image's trust store before
    anything is downloaded, and pip is pointed at that store, so a build behind an
    HTTPS scanner still verifies every certificate (decision 26)."""
    installs = " ".join(f"-r {rel}" for rel in relative_files)
    return (f"FROM {BASE_IMAGE}\n"
            "COPY . /req\n"
            f"RUN cat /req/{TRUST_NAME} >> {IMAGE_TRUST_STORE}\n"
            f"ENV PIP_CERT={IMAGE_TRUST_STORE}\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends git "
            "&& rm -rf /var/lib/apt/lists/*\n"
            "WORKDIR /req\n"
            f"RUN pip install --no-cache-dir {installs} -c {CONSTRAINTS_NAME}\n"
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


def build_image(root, constraints, context_dir, trust=None):
    """Build the checks image unless its tag exists. Returns (tag, built). The build
    context holds only the Dockerfile, the requirements files, the constraints file
    and the host's trusted roots (``trust``, by default ``host_trust_bundle()``).
    After a build, every package both the image and the constraints name must have
    the same version, or the image is removed and the build fails."""
    root = Path(root).resolve()
    trust = host_trust_bundle() if trust is None else trust
    files = requirements_files(root)
    tag = image_tag(root, files, constraints, trust)
    if image_exists(tag):
        return tag, False
    context = Path(context_dir)
    if context.exists():
        raise ContainerError(f"build context already exists: {context}")
    relative = [path.relative_to(root).as_posix() for path in files]
    for rel, path in zip(relative, files):
        (context / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, context / rel)
    shutil.copyfile(constraints, context / CONSTRAINTS_NAME)
    with open(context / TRUST_NAME, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(trust)
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
    clone = subprocess.run(["git", "clone", "--quiet", "--no-hardlinks",
                            "--config", "core.autocrlf=false",
                            "--config", "core.symlinks=false", str(root), str(dest)],
                           capture_output=True, encoding="utf-8", errors="replace",
                           env=_host_env())
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
