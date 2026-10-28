#!/usr/bin/env python3
"""Tests for container.py, orchestrator isolation stage S4 (plan section 5).

Docker is never started: ``container.docker`` is replaced by a fake that records
every command and answers from a script. The clean copy is made for real, from a
temporary git repository built for each test, so what git lists, ignores and tracks
is git's own answer. Expected values are stated by the tests, never computed by the
code under test.

    python workflows/review-orchestration/tests/test_container.py
"""

import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


container = _load("container")


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class FakeDocker:
    """Records each ``docker`` call's args; answers by the first rule whose prefix
    matches, else exit 0 with no output."""

    def __init__(self, rules=()):
        self.calls, self.rules, self.kwargs = [], list(rules), []

    def __call__(self, args, timeout=None, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        for prefix, answer in self.rules:
            if list(args[:len(prefix)]) == list(prefix):
                return answer(args) if callable(answer) else answer
        return done()


class DockerCase(unittest.TestCase):

    def fake(self, *rules):
        fake = FakeDocker(rules)
        patcher = mock.patch.object(container, "docker", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


_spec = importlib.util.spec_from_file_location(
    "fixture_git", Path(__file__).resolve().parent / "fixture_git.py")
fixture_git = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_git)
FixtureGitError = fixture_git.FixtureGitError


def git(cwd, *args):
    """Run a git command for a test's fixture and return its output, through the
    suites' one fixture runner (`fixture_git.py`)."""
    return fixture_git.run_git(["git", "-C", str(cwd), *args], cwd,
                               shown=f"git {' '.join(args)}").stdout


def write(path, data=b"x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# Docker state
# ---------------------------------------------------------------------------
class DockerStateTests(DockerCase):

    STATUS_RUNNING = done(out="Name                Value\nStatus              running\n")
    STATUS_STOPPED = done(out="Name                Value\nStatus              stopped\n")

    def test_negative_control_already_running_is_not_started(self):
        fake = self.fake((["desktop", "status"], self.STATUS_RUNNING))
        self.assertIs(container.ensure_running(sleep=self.fail), False)
        self.assertEqual(fake.calls, [["desktop", "status"]])

    def test_positive_stopped_is_started_and_waited_for(self):
        answers = iter([done(1), done(1), done(0)])
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED),
                         (["info"], lambda _args: next(answers)))
        slept = []
        self.assertIs(container.ensure_running(sleep=slept.append, clock=lambda: 0), True)
        self.assertEqual(fake.calls, [["desktop", "status"], ["desktop", "start"],
                                      ["info"], ["info"], ["info"]])
        self.assertEqual(slept, [2, 2])

    def test_rejection_running_said_elsewhere_than_the_status_line_is_not_running(self):
        self.fake((["desktop", "status"],
                   done(out="Status              stopped\nrunning containers 0\n")))
        self.assertIs(container.docker_running(), False)

    def test_rejection_a_failed_start_raises_with_the_reason(self):
        self.fake((["desktop", "status"], self.STATUS_STOPPED),
                  (["desktop", "start"], done(1, err="needs an update")))
        with self.assertRaisesRegex(container.ContainerError, "needs an update"):
            container.ensure_running(sleep=lambda _s: None)

    def test_rejection_an_engine_that_never_answers_raises_after_180_s(self):
        now = iter(range(0, 1000, 2))
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED),
                         (["info"], done(1)))
        with self.assertRaisesRegex(container.ContainerError, "within 180 s"):
            container.ensure_running(sleep=lambda _s: None, clock=lambda: next(now))
        # A poll every 2 s from 0 s; the 180 s check comes after the poll at 178 s.
        self.assertEqual(fake.calls.count(["info"]), 90)

    def test_rejection_docker_this_call_started_is_stopped_when_the_engine_never_answers(self):
        # Code review R26-2: the start succeeded, so the caller never learns it started
        # Docker; the call stops it again itself, once, before it raises.
        now = iter(range(0, 1000, 2))
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED),
                         (["info"], done(1)))
        with self.assertRaisesRegex(container.ContainerError,
                                    "within 180 s.*was stopped again"):
            container.ensure_running(sleep=lambda _s: None, clock=lambda: next(now))
        self.assertEqual(fake.calls.count(["desktop", "stop"]), 1)
        self.assertEqual(fake.calls[-1], ["desktop", "stop"])

    def test_rejection_an_info_that_times_out_also_stops_docker_again(self):
        def info(_args):
            raise container.ContainerError("`docker info` did not answer within 60 s")
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED), (["info"], info))
        with self.assertRaisesRegex(container.ContainerError,
                                    "did not answer within 60 s.*was stopped again"):
            container.ensure_running(sleep=lambda _s: None, clock=lambda: 0)
        self.assertEqual(fake.calls[-1], ["desktop", "stop"])

    def test_rejection_a_stop_that_fails_too_is_named(self):
        now = iter(range(0, 1000, 2))
        self.fake((["desktop", "status"], self.STATUS_STOPPED), (["info"], done(1)),
                  (["desktop", "stop"], done(1, err="busy")))
        with self.assertRaisesRegex(container.ContainerError,
                                    "within 180 s.*could not be stopped again.*busy"):
            container.ensure_running(sleep=lambda _s: None, clock=lambda: next(now))

    def test_negative_a_start_that_failed_stops_nothing(self):
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED),
                         (["desktop", "start"], done(1, err="needs an update")))
        with self.assertRaises(container.ContainerError):
            container.ensure_running(sleep=lambda _s: None)
        self.assertNotIn(["desktop", "stop"], fake.calls)

    def test_positive_stop_and_a_failed_stop(self):
        fake = self.fake()
        container.stop_docker()
        self.assertEqual(fake.calls, [["desktop", "stop"]])
        self.fake((["desktop", "stop"], done(1, err="busy")))
        with self.assertRaisesRegex(container.ContainerError, "busy"):
            container.stop_docker()


class HostEnvTests(unittest.TestCase):

    def test_positive_docker_runs_on_the_host_without_the_marker(self):
        with mock.patch.dict(os.environ, {"BOOK_DRAGON_CLEAN_COPY": "1"}), \
                mock.patch.object(container.subprocess, "run",
                                  return_value=done()) as spy:
            container.docker(["info"])
        self.assertEqual(spy.call_args.args[0], ["docker", "info"])
        self.assertNotIn("BOOK_DRAGON_CLEAN_COPY", spy.call_args.kwargs["env"])

    def timing_out(self):
        return mock.patch.object(container.subprocess, "run",
                                 side_effect=subprocess.TimeoutExpired(["docker"], 60))

    def test_rejection_a_command_past_its_time_is_a_container_error_naming_it(self):
        # Code review R11-1: a hung Docker Desktop command, at startup and in the build.
        for call, needle in ((container.docker_running, "docker desktop status"),
                             (lambda: container.ensure_running(sleep=lambda _s: None),
                              "docker desktop status"),
                             (lambda: container.image_exists("t"), "docker image inspect")):
            with self.subTest(needle=needle), self.timing_out():
                with self.assertRaisesRegex(container.ContainerError,
                                            f"`{needle}` did not answer within 60 s"):
                    call()

    def test_negative_the_container_run_still_receives_its_own_timeout(self):
        with self.timing_out():
            with self.assertRaises(subprocess.TimeoutExpired):
                container.docker(["run"], timeout=60, raise_timeout=True)

    def test_rejection_no_docker_is_a_container_error(self):
        with mock.patch.object(container.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(container.ContainerError):
                container.docker(["info"])


class EmptyStdinTests(unittest.TestCase):
    """Decision 37: every program the module starts gets an empty stdin, through
    ``_start``. The check helper's own stdin is the MCP pipe, and a child inheriting it
    held each check until the client closed the pipe (proof run 1, 2026-10-12)."""

    PROCESS_FUNCTIONS = {"run", "Popen", "call", "check_call", "check_output",
                         "getoutput", "getstatusoutput"}

    def recorded(self, call):
        """Run ``call`` with ``subprocess.run`` replaced by a recorder; the recorder
        answers as each program would, so the call completes."""
        seen = []

        def record(argv, **options):
            seen.append((argv, options))
            if argv[1:2] == ["-c"]:
                return done(out="[]")  # write_constraints' double-record probe
            return done(out="pkg==1\n")
        with mock.patch.object(container.subprocess, "run", side_effect=record):
            call()
        return seen

    def test_positive_every_start_has_an_empty_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = {
                "docker": lambda: container.docker(["info"]),
                "_git": lambda: container._git(tmp, "status"),
                "_clone": lambda: container._clone(Path(tmp) / "project",
                                                   Path(tmp) / "copy"),
                "write_constraints": lambda: container.write_constraints(
                    Path("py"), Path(tmp) / "constraints.txt"),
            }
            for name, call in calls.items():
                with self.subTest(name):
                    seen = self.recorded(call)
                    self.assertTrue(seen, f"{name} started no program")
                    for argv, options in seen:
                        self.assertIs(options.get("stdin"), subprocess.DEVNULL, argv)

    def test_rejection_a_callers_own_stdin_is_overridden(self):
        seen = self.recorded(lambda: container._start(["x"], stdin=None))
        self.assertIs(seen[0][1]["stdin"], subprocess.DEVNULL)

    def test_rejection_no_other_process_start_in_the_module(self):
        # Read off the parse tree: outside ``_start``, no call to a ``subprocess``
        # process function and no ``subprocess.run`` reference of any kind (a default
        # argument bound to it, as ``_clone``'s once was, included), and no ``os``
        # process start anywhere.
        import ast
        tree = ast.parse((SCRIPTS / "container.py").read_text(encoding="utf-8"))
        start, = [node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_start"]
        inside = {id(node) for node in ast.walk(start)}
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            if (node.value.id == "subprocess" and node.attr in self.PROCESS_FUNCTIONS
                    and id(node) not in inside):
                found.append(f"subprocess.{node.attr} at line {node.lineno}")
            if node.value.id == "os" and node.attr.startswith(
                    ("system", "popen", "spawn", "exec", "posix_spawn", "startfile")):
                found.append(f"os.{node.attr} at line {node.lineno}")
        self.assertEqual(found, [])
        self.assertIs(container._clone.__defaults__[0], container._start)
        self.assertIs(container.write_constraints.__defaults__[0], container._start)


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------
class ImageCase(DockerCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root = self.root = self.tmp / "project"
        # A decoy at the root: an included path resolved against the root rather
        # than the including file would pick it up instead of a/sub.txt.
        write(root / "requirements.txt", b"# top\n-r a/req.txt\n--requirement=b/req.txt\n")
        write(root / "a" / "req.txt", b"-r sub.txt\nalpha==1.0\n")
        write(root / "a" / "sub.txt", b"beta==2.0\n")
        write(root / "sub.txt", b"decoy==9\n")
        write(root / "b" / "req.txt", b"-r../a/sub.txt\ngamma==3.0\n")
        write(root / "workflows" / "review-orchestration" / "requirements.txt",
              b"sdk==1.1\n")
        self.constraints = self.tmp / "constraints.txt"
        write(self.constraints, b"alpha==1.0\nbeta==2.0\npywin32==311\n")


class RequirementsTests(ImageCase):

    def test_positive_every_include_in_order_each_relative_to_its_includer(self):
        rel = [p.relative_to(self.root.resolve()).as_posix()
               for p in container.requirements_files(self.root)]
        self.assertEqual(rel, ["requirements.txt", "a/req.txt", "a/sub.txt", "b/req.txt",
                               "workflows/review-orchestration/requirements.txt"])

    def test_rejection_a_missing_include_is_refused(self):
        write(self.root / "a" / "req.txt", b"-r gone.txt\n")
        with self.assertRaisesRegex(container.ContainerError, "gone.txt"):
            container.requirements_files(self.root)

    def test_rejection_an_include_outside_the_project_is_refused(self):
        write(self.tmp / "outside.txt", b"x==1\n")
        write(self.root / "a" / "req.txt", b"-r ../../outside.txt\n")
        with self.assertRaisesRegex(container.ContainerError, "outside the project"):
            container.requirements_files(self.root)


# The Dockerfile for the two files TagTests hash, written out here rather than taken
# from the code under test (decision 41: the packages come only from the wheels).
TAG_DOCKERFILE = (
    "FROM python:3.13-slim\n"
    "ENV PIP_DISABLE_PIP_VERSION_CHECK=1\n"
    "COPY . /req\n"
    "RUN apt-get update && apt-get install -y --no-install-recommends git "
    "&& rm -rf /var/lib/apt/lists/*\n"
    "WORKDIR /req\n"
    "RUN --network=none pip install --no-cache-dir --no-index --find-links /req/wheels "
    "-r requirements.txt -r a/req.txt -c constraints.txt\n"
    "RUN useradd -m checker && git config --system safe.directory '*'\n"
    "USER checker\n"
    "WORKDIR /work\n")


class TagTests(ImageCase):

    def expected(self, constraints_bytes):
        digest = hashlib.sha256()
        for rel in ("requirements.txt", "a/req.txt"):
            digest.update(rel.encode() + b"\0" + (self.root / rel).read_bytes() + b"\0")
        digest.update(constraints_bytes + b"\0" + TAG_DOCKERFILE.encode())
        return "bookdragon-checks:" + digest.hexdigest()[:12]

    def test_positive_the_tag_is_the_hash_of_the_files_constraints_and_dockerfile(self):
        files = [self.root / "requirements.txt", self.root / "a" / "req.txt"]
        self.assertEqual(container.image_tag(self.root, files, self.constraints),
                         self.expected(self.constraints.read_bytes()))

    def test_positive_any_input_changing_changes_the_tag(self):
        files = [self.root / "requirements.txt", self.root / "a" / "req.txt"]
        before = container.image_tag(self.root, files, self.constraints)
        self.assertNotEqual(before, container.image_tag(self.root, files[::-1],
                                                        self.constraints))
        with mock.patch.object(container, "dockerfile", lambda rel: "FROM other\n"):
            self.assertNotEqual(before, container.image_tag(self.root, files,
                                                            self.constraints))
        write(self.constraints, b"alpha==1.1\n")
        self.assertNotEqual(before, container.image_tag(self.root, files,
                                                        self.constraints))

    def test_negative_control_nothing_about_the_host_is_in_the_tag(self):
        # Decision 41: a Windows certificate update rebuilt the image under decision
        # 26. The tag takes no host input, so the host's certificate store, which the
        # code no longer reads, cannot move it.
        self.assertEqual(list(inspect.signature(container.image_tag).parameters),
                         ["root", "files", "constraints"])
        self.assertFalse(hasattr(container, "host_trust_bundle"))
        files = [self.root / "requirements.txt", self.root / "a" / "req.txt"]
        with mock.patch("ssl.enum_certificates", create=True, return_value=[]):
            self.assertEqual(container.image_tag(self.root, files, self.constraints),
                             self.expected(self.constraints.read_bytes()))


def wheel_entry(filename, data, packagetype="bdist_wheel"):
    return {"filename": filename, "packagetype": packagetype,
            "url": f"https://files.pythonhosted.org/packages/ab/{filename}",
            "digests": {"sha256": hashlib.sha256(data).hexdigest()}}


class FakePyPI:
    """Answers ``get(url)`` from release records and files, recording every URL."""

    def __init__(self, releases, files):
        self.releases, self.files, self.urls = releases, files, []

    def __call__(self, url):
        self.urls.append(url)
        for (name, version), entries in self.releases.items():
            if url == f"https://pypi.org/pypi/{name}/{version}/json":
                return json.dumps({"urls": entries}).encode()
        for entry_url, data in self.files.items():
            if url == entry_url:
                return data
        raise OSError(f"no such page: {url}")


def pip_image_tags():
    """The tags pip in python:3.13-slim lists, most preferred first, as the image
    gave them on 2026-10-28 (1,205 tags, the same set and order): produced here by
    pip's own ``packaging`` for CPython 3.13 on x86-64 Linux with glibc 2.41, its
    manylinux list generated by pip given only that glibc, so no tag is written by
    hand. A stand-in for ``container.image_tags()``, which asks the image itself."""
    import itertools
    from pip._vendor.packaging import _manylinux, tags
    with mock.patch.object(_manylinux, "_get_glibc_version", return_value=(2, 41)), \
            mock.patch.object(_manylinux, "_have_compatible_abi", return_value=True,
                              create=True):
        platforms = list(_manylinux.platform_tags(["x86_64"])) + ["linux_x86_64"]
    return [str(t) for t in itertools.chain(
        tags.cpython_tags((3, 13), ["cp313"], platforms),
        tags.compatible_tags((3, 13), "cp313", platforms))]


IMAGE_TAGS = pip_image_tags()
RANKS = {tag: n for n, tag in reversed(list(enumerate(IMAGE_TAGS)))}


class ImageTagsTests(DockerCase):
    """Code review R50-1: the tags come from pip in the image, asked offline."""

    def answer(self, out, code=0, err=""):
        return self.fake((["run"], done(code, out=out, err=err)))

    @staticmethod
    def framed(tags):
        return f"COUNT {len(tags)}\n" + "".join(f"{t}\n" for t in tags) + "END\n"

    def test_positive_the_image_is_asked_offline_and_its_list_kept_in_order(self):
        fake = self.answer(self.framed(IMAGE_TAGS))
        self.assertEqual(container.image_tags(), IMAGE_TAGS)
        self.assertEqual(len(fake.calls), 1)
        argv = fake.calls[0]
        self.assertEqual(argv[:6], ["run", "--rm", "--network", "none",
                                    "python:3.13-slim", "python"])
        self.assertEqual(argv[6], "-c")
        self.assertIn("from pip._vendor.packaging.tags import sys_tags", argv[7])

    def test_rejection_an_answer_that_is_not_a_whole_tag_list_is_refused(self):
        whole = self.framed(IMAGE_TAGS)
        keep = IMAGE_TAGS.index("py3-none-any") + 1
        cases = [("", 0), (whole, 1),
                 ("\n".join(IMAGE_TAGS) + "\n", 0),                # not framed
                 (whole.rsplit("END", 1)[0], 0),                   # end line missing
                 # Code review R51-3: cut short after py3-none-any, end line kept.
                 (f"COUNT {len(IMAGE_TAGS)}\n" + "\n".join(IMAGE_TAGS[:keep]) + "\nEND\n", 0),
                 (self.framed([]), 0),
                 (self.framed(["cp313-cp313-linux_x86_64"]), 0),   # no py3-none-any
                 (self.framed(["py3-none-any", "Traceback (most recent call last)"]), 0)]
        for out, code in cases:
            with self.subTest(out=out[:60], code=code):
                self.answer(out, code, err="boom")
                with self.assertRaisesRegex(container.ContainerError,
                                            "did not list the tags it installs"):
                    container.image_tags()

    def test_negative_control_the_hand_written_rule_is_gone(self):
        for name in ("image_wheel_ok", "IMAGE_GLIBC", "IMAGE_PYTHON_MINOR",
                     "OLDEST_MANYLINUX", "_tags_ok", "_platform_ok"):
            self.assertFalse(hasattr(container, name), name)


class WheelRankTests(unittest.TestCase):
    """A wheel is matched against the image's own tags, each tag as written."""

    def rank(self, name):
        return container.wheel_rank(name, RANKS)

    def test_positive_wheels_the_image_installs(self):
        for name in ("pure-1.0-py3-none-any.whl", "pure-1.0-py2.py3-none-any.whl",
                     "pure-1.0-py313-none-any.whl",
                     "c-1.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                     "c-1.0-cp313-cp313-manylinux_2_41_x86_64.whl",
                     "c-1.0-cp313-cp313-manylinux1_x86_64.whl",
                     "c-1.0-cp313-cp313-linux_x86_64.whl",
                     "stable-1.0-cp39-abi3-manylinux_2_28_x86_64.whl",
                     "stable-1.0-cp32-abi3-manylinux1_x86_64.whl",
                     "c-1.0-cp313-none-any.whl",
                     # pip lowercases a wheel name's tags before it compares them.
                     "c-1.0-CP313-cp313-manylinux_2_17_x86_64.whl",
                     "built-1.0-1-py3-none-any.whl"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.rank(name))

    def test_positive_every_answer_is_pips_own_for_a_grid_of_names(self):
        # The match is checked against pip's own reading of each name
        # (parse_wheel_filename) and the image's tags, across case, leading zeros,
        # compressed sets and every manylinux glibc from 2.0 to 2.45.
        from pip._vendor.packaging.utils import InvalidWheelFilename, parse_wheel_filename
        wanted = set(IMAGE_TAGS)
        pythons = ("py3", "py2.py3", "py313", "py314", "cp313", "CP313", "cp32", "cp31")
        abis = ("none", "abi3", "cp313", "ABI3", "cp313t")
        shapes = ([f"manylinux_2_{n}_x86_64" for n in range(0, 46)]
                  + ["manylinux_2_05_x86_64", "manylinux_2_017_x86_64", "any", "ANY",
                     "linux_x86_64", "manylinux1_x86_64", "manylinux2014_x86_64",
                     "manylinux_2_17_x86_64.manylinux2014_x86_64", "win_amd64"])
        disagree = []
        for p in pythons:
            for a in abis:
                for s in shapes:
                    name = f"x-1.0-{p}-{a}-{s}.whl"
                    try:
                        pips = bool({str(t) for t in parse_wheel_filename(name)[3]} & wanted)
                    except InvalidWheelFilename:
                        pips = False
                    if (self.rank(name) is not None) != pips:
                        disagree.append(name)
        self.assertEqual(disagree, [])

    def test_positive_a_wheel_ranks_by_its_best_tag_as_pip_prefers(self):
        # Compressed tag sets expand; the best-placed tag gives the rank.
        both = "c-1.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        self.assertEqual(self.rank(both), IMAGE_TAGS.index("cp313-cp313-manylinux_2_17_x86_64"))
        self.assertLess(self.rank("c-1.0-cp313-cp313-manylinux_2_17_x86_64.whl"),
                        self.rank("c-1.0-cp313-abi3-manylinux_2_17_x86_64.whl"))
        self.assertLess(self.rank("c-1.0-cp313-abi3-manylinux_2_17_x86_64.whl"),
                        self.rank("pure-1.0-py3-none-any.whl"))

    def test_rejection_wheels_the_image_cannot_install(self):
        for name in ("c-1.0-cp313-cp313-win_amd64.whl",
                     "c-1.0-cp313-cp313-macosx_11_0_arm64.whl",
                     "c-1.0-cp313-cp313-manylinux_2_42_x86_64.whl",
                     "c-1.0-cp313-cp313-manylinux_2_17_aarch64.whl",
                     "c-1.0-cp313-cp313-musllinux_1_2_x86_64.whl",
                     "c-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
                     "c-1.0-cp314-cp314-manylinux_2_17_x86_64.whl",
                     "c-1.0-cp313-cp313t-manylinux_2_17_x86_64.whl",
                     "stable-1.0-cp314-abi3-manylinux_2_28_x86_64.whl",
                     "pure-1.0-py2-none-any.whl", "pure-1.0-py314-none-any.whl",
                     # Code review R48-2: `any` goes with ABI `none` only, and abi3
                     # starts at CPython 3.2.
                     "c-1.0-cp313-cp313-any.whl", "c-1.0-cp313-abi3-any.whl",
                     "c-1.0-cp312-none-any.whl",
                     "stable-1.0-cp31-abi3-manylinux_2_17_x86_64.whl",
                     # Code review R49-1: pip names no manylinux glibc below 2.5.
                     "c-1.0-cp313-cp313-manylinux_2_4_x86_64.whl",
                     "c-1.0-cp313-cp313-manylinux_2_0_x86_64.whl",
                     # Code review R50-1: a tag is matched as pip reads it, which
                     # keeps a leading zero.
                     "c-1.0-cp313-cp313-manylinux_2_05_x86_64.whl",
                     "pure-1.0.tar.gz", "short-py3-none-any.whl"):
            with self.subTest(name=name):
                self.assertIsNone(self.rank(name))


class WheelStoreTests(unittest.TestCase):
    """Decision 41: each pinned release's wheel comes from its own PyPI record,
    checked by its SHA-256, and is reused; PyPI's index pages are never asked."""

    LINUX = b"linux wheel"
    WINDOWS = b"windows wheel"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = self.tmp / "store"
        self.constraints = self.tmp / "constraints.txt"
        write(self.constraints, b"alpha==1.0\npywin32==311\n")
        self.linux = wheel_entry("alpha-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", self.LINUX)
        self.windows = wheel_entry("alpha-1.0-cp313-cp313-win_amd64.whl", self.WINDOWS)
        self.pypi = FakePyPI(
            {("alpha", "1.0"): [self.windows, self.linux],
             ("pywin32", "311"): [wheel_entry("pywin32-311-cp313-cp313-win_amd64.whl", b"w")]},
            {self.linux["url"]: self.LINUX, self.windows["url"]: self.WINDOWS})

    def fill(self, get=None, run=None):
        return container.fill_wheel_store(self.constraints, self.store, "py", IMAGE_TAGS,
                                          get=get or self.pypi, run=run or self.no_run)

    def release(self, *entries):
        """Make alpha==1.0's record ``entries``, each a (filename, data, extra) triple."""
        made = []
        for filename, data, extra in entries:
            entry = dict(wheel_entry(filename, data), **extra)
            self.pypi.files[entry["url"]] = data
            made.append(entry)
        self.pypi.releases[("alpha", "1.0")] = made
        return made

    def test_rejection_a_wheel_not_plainly_named_for_this_release_is_never_used(self):
        # Code review R51-1: a name pip would refuse, or another project's or
        # version's, is not used; the good one is taken.
        good = "alpha-1.0-py3-none-any.whl"
        for bad in ("alpha-1.0-bad-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "alpha-1.0-cp313-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "alpha-1.0.post1-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "beta-1.0-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "alpha-1.01-cp313-cp313-manylinux_2_17_x86_64.whl"):
            with self.subTest(bad=bad):
                self.store = self.tmp / f"store-{len(bad)}-{bad[:14]}"
                self.release((bad, b"bad", {}), (good, b"good", {}))
                wheels, _ = self.fill()
                self.assertEqual(wheels[0].name, good)

    def test_positive_a_plain_build_tag_and_a_differently_spelt_name_are_used(self):
        self.constraints.write_text("Alpha.Lib==1.0\n", encoding="utf-8", newline="\n")
        name = "alpha_lib-1.0-1-cp313-cp313-manylinux_2_17_x86_64.whl"
        entry = wheel_entry(name, b"built")
        self.pypi.releases[("Alpha.Lib", "1.0")] = [entry]
        self.pypi.files[entry["url"]] = b"built"
        wheels, _ = self.fill()
        self.assertEqual([w.name for w in wheels], [name])

    def test_rejection_a_project_name_with_a_doubled_or_stray_separator_is_never_used(self):
        # Code review R52-1: pip refuses `__` in a wheel's project name; a doubled,
        # leading or trailing separator is not plain, so none is used even when it
        # ranks above the plain one.
        self.constraints.write_text("Alpha.Lib==1.0\n", encoding="utf-8", newline="\n")
        good = "alpha_lib-1.0-py3-none-any.whl"
        for bad in ("alpha__lib-1.0-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "alpha._lib-1.0-cp313-cp313-manylinux_2_17_x86_64.whl",
                    "alpha_lib_-1.0-cp313-cp313-manylinux_2_17_x86_64.whl"):
            with self.subTest(bad=bad):
                self.store = self.tmp / f"store-{bad[:12]}"
                entries = [wheel_entry(bad, b"bad"), wheel_entry(good, b"good")]
                self.pypi.releases[("Alpha.Lib", "1.0")] = entries
                self.pypi.files.update({entries[0]["url"]: b"bad", entries[1]["url"]: b"good"})
                wheels, _ = self.fill()
                self.assertEqual([w.name for w in wheels], [good])
        # A leading separator is not even a plain file name: the whole record is
        # refused, the stricter outcome.
        self.pypi.releases[("Alpha.Lib", "1.0")] = [
            wheel_entry("_alpha_lib-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", b"x"),
            wheel_entry(good, b"good")]
        with self.assertRaisesRegex(container.ContainerError, "will not take"):
            self.fill()

    def test_rejection_a_yanked_file_is_never_used(self):
        # Code review R51-2: a yanked wheel is passed over even when it ranks
        # first; a release whose only usable wheel is yanked has none.
        self.release(("alpha-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", b"yanked",
                      {"yanked": True}), ("alpha-1.0-py3-none-any.whl", b"pure", {}))
        wheels, _ = self.fill()
        self.assertEqual(wheels[0].name, "alpha-1.0-py3-none-any.whl")
        self.store = self.tmp / "store-only-yanked"
        self.release(("alpha-1.0-py3-none-any.whl", b"pure", {"yanked": True}))
        wheels, skipped = self.fill()
        self.assertEqual((wheels, skipped), ([], ["alpha==1.0", "pywin32==311"]))

    def test_rejection_wheels_that_rank_equally_are_refused_not_guessed(self):
        # Code review R51-2: two builds with the same tags; pip would choose by build
        # tag, which this build does not model, so it stops and names both.
        one = "alpha-1.0-1-cp313-cp313-manylinux_2_17_x86_64.whl"
        two = "alpha-1.0-2-cp313-cp313-manylinux_2_17_x86_64.whl"
        self.release((one, b"one", {}), (two, b"two", {}))
        with self.assertRaisesRegex(container.ContainerError,
                                    rf"alpha==1\.0 has 2 wheels .*{one}.*{two}"):
            self.fill()
        self.assertFalse(self.store.exists() and list(self.store.glob("*.whl")))

    def test_positive_of_several_usable_wheels_the_one_pip_prefers_is_taken(self):
        # Code review R50-1: chosen by the image's own preference, not by name. The
        # cp313-cp313 wheel sorts after the abi3 one by name but pip prefers it.
        stable = wheel_entry("alpha-1.0-cp313-abi3-manylinux_2_17_x86_64.whl", b"stable")
        exact = wheel_entry("alpha-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", b"exact")
        pure = wheel_entry("alpha-1.0-py3-none-any.whl", b"pure")
        self.pypi.releases[("alpha", "1.0")] = [pure, stable, exact, self.windows]
        self.pypi.files.update({e["url"]: d for e, d in ((stable, b"stable"),
                                                       (exact, b"exact"), (pure, b"pure"))})
        wheels, _ = self.fill()
        self.assertEqual(wheels[0].name, exact["filename"])
        self.assertEqual(wheels[0].read_bytes(), b"exact")

    def no_run(self, *_a, **_k):
        self.fail("nothing is built when a wheel exists")

    def test_positive_the_linux_wheel_is_stored_and_a_windows_only_release_skipped(self):
        wheels, skipped = self.fill()
        self.assertEqual(wheels, [self.store / self.linux["filename"]])
        self.assertEqual(wheels[0].read_bytes(), self.LINUX)
        self.assertEqual(skipped, ["pywin32==311"])
        self.assertNotIn(self.windows["url"], self.pypi.urls)

    def test_positive_only_release_records_and_their_files_are_asked_for(self):
        self.fill()
        self.assertEqual(self.pypi.urls, ["https://pypi.org/pypi/alpha/1.0/json",
                                          self.linux["url"],
                                          "https://pypi.org/pypi/pywin32/311/json"])
        self.assertFalse([u for u in self.pypi.urls if "/simple/" in u])

    def test_negative_control_a_stored_wheel_with_the_right_hash_is_not_downloaded(self):
        self.fill()
        self.pypi.urls.clear()
        self.fill()
        self.assertNotIn(self.linux["url"], self.pypi.urls)

    def test_rejection_a_stored_wheel_with_the_wrong_hash_is_downloaded_again(self):
        write(self.store / self.linux["filename"], b"tampered")
        wheels, _ = self.fill()
        self.assertIn(self.linux["url"], self.pypi.urls)
        self.assertEqual(wheels[0].read_bytes(), self.LINUX)

    def test_rejection_a_download_that_does_not_match_its_hash_is_refused_and_not_kept(self):
        self.pypi.files[self.linux["url"]] = b"not what PyPI published"
        with self.assertRaisesRegex(container.ContainerError,
                                    r"alpha-1\.0.*does not match the SHA-256.*nothing was kept"):
            self.fill()
        self.assertEqual(list(self.store.rglob("*")) if self.store.exists() else [], [])

    def test_rejection_a_file_entry_with_an_unsafe_name_or_host_is_refused(self):
        bad = [dict(self.linux, filename="../escape-1.0-py3-none-any.whl"),
               dict(self.linux, url="https://example.com/alpha.whl"),
               dict(self.linux, digests={})]
        for entry in bad:
            with self.subTest(entry=entry):
                self.pypi.releases[("alpha", "1.0")] = [entry]
                with self.assertRaisesRegex(container.ContainerError, "will not take"):
                    self.fill()
        self.assertFalse((self.tmp / "escape-1.0-py3-none-any.whl").exists())

    def test_rejection_a_fetch_is_tried_three_times_then_names_the_release(self):
        calls = []

        def failing(url):
            calls.append(url)
            raise OSError("Connection broken: InvalidChunkLength")
        with self.assertRaisesRegex(container.ContainerError,
                                    r"PyPI record for alpha==1\.0.*3 tries.*InvalidChunkLength"):
            self.fill(get=failing)
        self.assertEqual(calls, ["https://pypi.org/pypi/alpha/1.0/json"] * 3)

    def test_positive_a_fetch_that_fails_once_is_retried(self):
        failures = []

        def flaky(url):
            if not failures:
                failures.append(url)
                raise OSError("reset")
            return self.pypi(url)
        wheels, _ = self.fill(get=flaky)
        self.assertEqual(len(wheels), 1)

    def test_rejection_an_unreadable_release_record_names_the_release(self):
        for body in (b"<html>", b'{"urls": 3}', b"{}"):
            with self.subTest(body=body):
                with self.assertRaisesRegex(container.ContainerError,
                                            r"PyPI record for alpha==1\.0 could not be read"):
                    self.fill(get=lambda url, b=body: b)

    def source_release(self):
        source = b"source archive"
        entry = wheel_entry("sgml-1.0.tar.gz", source, packagetype="sdist")
        write(self.constraints, b"sgml==1.0\n")
        self.pypi.releases[("sgml", "1.0")] = [entry]
        self.pypi.files[entry["url"]] = source
        return entry

    def builder(self, name="sgml-1.0-py3-none-any.whl", code=0):
        calls = []

        def run(argv, **_kw):
            calls.append(argv)
            out = Path(argv[argv.index("-w") + 1])
            if code == 0:
                write(out / name, b"built wheel")
            return done(code, err="" if code == 0 else "setuptools missing")
        return run, calls

    def test_positive_a_source_only_release_is_built_with_nothing_fetched_for_the_build(self):
        entry = self.source_release()
        run, calls = self.builder()
        wheels, skipped = self.fill(run=run)
        self.assertEqual((len(wheels), skipped), (1, []))
        self.assertEqual(wheels[0].name, "sgml-1.0-py3-none-any.whl")
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[:4], ["py", "-m", "pip", "wheel"])
        for flag in ("--no-deps", "--no-build-isolation", "--no-index"):
            self.assertIn(flag, argv)
        self.assertEqual(Path(argv[-1]), self.store / "source" / entry["filename"])

    def test_negative_control_a_built_wheel_is_reused_without_a_second_build(self):
        self.source_release()
        run, calls = self.builder()
        self.fill(run=run)
        self.fill(run=run)
        self.assertEqual(len(calls), 1)

    def test_rejection_a_built_wheel_that_cannot_be_proved_is_built_again(self):
        # Code review R48-1: a built wheel has no PyPI digest, so it is reused only
        # while its record matches both the source the release lists and the wheel.
        def altered(folder):
            next(folder.glob("*.whl")).write_bytes(b"altered wheel")

        def unrecorded(folder):
            (folder / "built.json").unlink()

        def garbled(folder):
            (folder / "built.json").write_text("{not json", encoding="utf-8", newline="\n")

        def new_source(_folder):
            source = b"a different source archive"
            entry = wheel_entry("sgml-1.0.tar.gz", source, packagetype="sdist")
            self.pypi.releases[("sgml", "1.0")] = [entry]
            self.pypi.files[entry["url"]] = source
        for change in (altered, unrecorded, garbled, new_source):
            with self.subTest(change=change.__name__):
                self.store = self.tmp / f"store-{change.__name__}"
                self.source_release()
                run, calls = self.builder()
                wheels, _ = self.fill(run=run)
                change(wheels[0].parent)
                again, _ = self.fill(run=run)
                self.assertEqual(len(calls), 2)
                self.assertEqual(again[0].read_bytes(), b"built wheel")
                record = json.loads((again[0].parent / "built.json").read_text(encoding="utf-8"))
                self.assertEqual(record["wheel_sha256"],
                                 hashlib.sha256(b"built wheel").hexdigest())
                self.assertEqual(len(list(again[0].parent.glob("*.whl"))), 1)

    def test_positive_a_build_records_the_source_and_wheel_hashes(self):
        entry = self.source_release()
        run, _calls = self.builder()
        wheels, _ = self.fill(run=run)
        record = json.loads((wheels[0].parent / "built.json").read_text(encoding="utf-8"))
        self.assertEqual(record, {"source_sha256": entry["digests"]["sha256"],
                                  "wheel": "sgml-1.0-py3-none-any.whl",
                                  "wheel_sha256": hashlib.sha256(b"built wheel").hexdigest()})

    def test_rejection_a_source_build_that_fails_or_gives_a_platform_wheel_is_refused(self):
        self.source_release()
        for run, message in ((self.builder(code=1)[0], "could not be built.*setuptools"),
                             (self.builder("sgml-1.0-cp313-cp313-win_amd64.whl")[0],
                              "which the image cannot install")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(container.ContainerError, message):
                    self.fill(run=run)
        self.assertFalse((self.store / "built").exists()
                         and list((self.store / "built").rglob("*.whl")))

    def test_positive_the_store_is_outside_the_project(self):
        store = container.wheel_store()
        self.assertEqual(store, Path(tempfile.gettempdir()) / "book-dragon-wheels")
        with self.assertRaises(ValueError):
            store.resolve().relative_to(SCRIPTS.parent.parent.parent.resolve())


class BuildTests(ImageCase):

    FREEZE_OK = done(out="alpha==1.0\nbeta==2.0\nsdk==1.1\nsetuptools==80\n")

    def wheels(self):
        """A ``wheels`` callable over two stored wheels, counting its calls."""
        self.wheel_calls = getattr(self, "wheel_calls", 0)
        paths = []
        for name in ("alpha-1.0-py3-none-any.whl", "beta-2.0-py3-none-any.whl"):
            write(self.tmp / "store" / name, name.encode())
            paths.append(self.tmp / "store" / name)

        def call():
            self.wheel_calls += 1
            return paths, ["pywin32==311"]
        return call

    def build(self, *rules):
        fake = self.fake((["image", "inspect"], done(1)), *rules)
        ctx = self.tmp / "ctx"
        return fake, ctx, container.build_image(self.root, self.constraints, ctx,
                                                self.wheels())

    def test_positive_build_installs_each_requirements_file_with_the_constraints(self):
        fake, ctx, (tag, built) = self.build(
            (["run", "--rm", "--network", "none"], self.FREEZE_OK))
        self.assertIs(built, True)
        staging = f"{tag}-unverified"
        # Built and checked under the staging tag; the real tag only after the check
        # passes; then the staging name dropped (code review R13-1).
        self.assertEqual([c for c in fake.calls if c[0] != "image" or c[1] != "inspect"], [
            ["build", "-t", staging, str(ctx)],
            ["run", "--rm", "--network", "none", staging, "pip", "freeze"],
            ["tag", staging, tag],
            ["image", "rm", staging]])
        dockerfile = (ctx / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("RUN --network=none pip install --no-cache-dir --no-index "
                      "--find-links /req/wheels -r requirements.txt -r a/req.txt "
                      "-r a/sub.txt -r b/req.txt -r workflows/review-orchestration/"
                      "requirements.txt -c constraints.txt\n", dockerfile)
        self.assertNotIn("-r constraints.txt", dockerfile)
        self.assertEqual(sorted(p.relative_to(ctx).as_posix() for p in ctx.rglob("*")
                                if p.is_file()),
                         ["Dockerfile", "a/req.txt", "a/sub.txt", "b/req.txt",
                          "constraints.txt", "requirements.txt",
                          "wheels/alpha-1.0-py3-none-any.whl",
                          "wheels/beta-2.0-py3-none-any.whl",
                          "workflows/review-orchestration/requirements.txt"])
        self.assertEqual((ctx / "wheels" / "alpha-1.0-py3-none-any.whl").read_bytes(),
                         b"alpha-1.0-py3-none-any.whl")

    def test_positive_the_install_uses_no_index_no_network_and_no_host_certificate(self):
        # Decision 41, replacing decision 26: nothing in the install talks to PyPI,
        # so the image carries none of the host's certificates.
        _fake, ctx, _result = self.build(
            (["run", "--rm", "--network", "none"], self.FREEZE_OK))
        lines = (ctx / "Dockerfile").read_text(encoding="utf-8").splitlines()
        pip = [l for l in lines if " pip install " in l]
        self.assertEqual(len(pip), 1)
        self.assertTrue(pip[0].startswith("RUN --network=none pip install "))
        self.assertIn(" --no-index ", pip[0])
        self.assertIn(" --find-links /req/wheels ", pip[0])
        text = "\n".join(lines)
        for absent in ("PIP_CERT", "host-roots", "ca-certificates", "BEGIN CERTIFICATE",
                       "--index-url", "--extra-index-url", "--trusted-host"):
            self.assertNotIn(absent, text)
        self.assertIn("USER checker", lines)
        self.assertEqual(lines[0], "FROM python:3.13-slim")

    def test_negative_control_an_existing_tag_is_not_rebuilt_or_fetched_for(self):
        fake = self.fake((["image", "inspect"], done(0)))
        tag, built = container.build_image(self.root, self.constraints, self.tmp / "ctx",
                                           self.wheels())
        self.assertIs(built, False)
        self.assertEqual([c[0] for c in fake.calls], ["image"])
        self.assertFalse((self.tmp / "ctx").exists())
        self.assertEqual(self.wheel_calls, 0)

    def test_rejection_a_store_that_cannot_be_filled_refuses_before_any_build(self):
        fake = self.fake((["image", "inspect"], done(1)))

        def failing():
            raise container.ContainerError("mcp==1.28.1 could not be downloaded")
        with self.assertRaisesRegex(container.ContainerError, "mcp==1.28.1"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx", failing)
        self.assertEqual([c for c in fake.calls if c[0] == "build"], [])

    def test_rejection_a_version_that_differs_fails_the_build_and_removes_the_image(self):
        fake = self.fake((["image", "inspect"], done(1)),
                         (["run", "--rm", "--network", "none"],
                          done(out="alpha==1.0\nbeta==2.1\n")))
        with self.assertRaisesRegex(container.ContainerError, "beta 2.0 != 2.1"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx",
                                  self.wheels())
        self.assertEqual(fake.calls[-1][:3], ["image", "rm", "-f"])
        self.assertTrue(fake.calls[-1][3].endswith("-unverified"))
        self.assertEqual([c for c in fake.calls if c[0] == "tag"], [])

    def failed_check(self, freeze, remove):
        fake = self.fake((["image", "inspect"], done(1)),
                         (["run", "--rm", "--network", "none"], freeze),
                         (["image", "rm", "-f"], remove))
        ctx = Path(tempfile.mkdtemp(dir=self.tmp)) / "ctx"  # a fresh folder each call
        with self.assertRaises(container.ContainerError) as caught:
            container.build_image(self.root, self.constraints, ctx, self.wheels())
        removals = [c for c in fake.calls if c[:3] == ["image", "rm", "-f"]]
        return str(caught.exception), removals

    @staticmethod
    def hung(_args):
        raise container.ContainerError("`docker run --rm` did not answer within 300 s")

    def test_rejection_a_freeze_that_does_not_answer_still_removes_the_image(self):
        # Code review R12-2: the timeout used to skip the clean-up.
        message, removals = self.failed_check(self.hung, done(0))
        self.assertEqual(len(removals), 1)
        self.assertIn("did not answer within 300 s", message)
        self.assertIn("the image was removed", message)

    def test_rejection_a_removal_refused_or_unanswered_is_reported_not_hidden(self):
        mismatch = done(out="alpha==1.0\nbeta==2.1\n")
        for remove in (done(1, err="in use"), self.hung):
            with self.subTest(remove=remove):
                message, removals = self.failed_check(mismatch, remove)
                staging = removals[0][3]
                self.assertTrue(staging.endswith("-unverified"))
                self.assertNotIn("was removed", message)
                self.assertIn(f"docker image rm -f {staging}", message)
                self.assertIn("it is never reused", message)

    def test_rejection_a_freeze_that_fails_or_lists_nothing_fails_the_build(self):
        for answer in (done(1, out="alpha==1.0\n"), done(0, out="")):
            with self.subTest(answer=answer):
                fake = self.fake((["image", "inspect"], done(1)),
                                 (["run", "--rm", "--network", "none"], answer))
                ctx = self.tmp / f"ctx-{len(fake.calls)}-{answer.returncode}"
                with self.assertRaisesRegex(container.ContainerError, "version check"):
                    container.build_image(self.root, self.constraints, ctx, self.wheels())
                self.assertEqual(fake.calls[-1][:3], ["image", "rm", "-f"])
                self.assertEqual([c for c in fake.calls if c[0] == "tag"], [])

    def test_rejection_after_a_failed_removal_the_next_run_does_not_reuse_the_image(self):
        # Code review R13-1: two runs in a row, Docker keeping what it is told to.
        images = set()

        def build(args):
            images.add(args[2])
            return done()

        def inspect(args):
            return done(0 if args[2] in images else 1)

        def tag(args):
            images.add(args[2])
            return done()
        fake = self.fake((["image", "inspect"], inspect), (["build"], build),
                         (["run", "--rm", "--network", "none"], done(out="beta==2.1\n")),
                         (["image", "rm", "-f"], done(1, err="in use")), (["tag"], tag))
        for n in (1, 2):
            with self.assertRaisesRegex(container.ContainerError, "version check"):
                container.build_image(self.root, self.constraints, self.tmp / f"ctx-{n}",
                                      self.wheels())
        self.assertEqual(len([c for c in fake.calls if c[0] == "build"]), 2)
        self.assertEqual([c for c in fake.calls if c[0] == "tag"], [])
        self.assertTrue(all(i.endswith("-unverified") for i in images))

    def test_rejection_a_checked_image_that_cannot_be_tagged_is_refused(self):
        self.fake((["image", "inspect"], done(1)),
                  (["run", "--rm", "--network", "none"], self.FREEZE_OK),
                  (["tag"], done(1, err="no space")))
        with self.assertRaisesRegex(container.ContainerError, "could not be tagged.*no space"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx",
                                  self.wheels())

    def test_negative_a_windows_only_constraint_absent_from_the_image_is_fine(self):
        # pywin32 is in the constraints only; versions are compared where both name it.
        _fake, _ctx, (_tag, built) = self.build(
            (["run", "--rm", "--network", "none"], self.FREEZE_OK))
        self.assertIs(built, True)

    def test_rejection_a_failed_build_raises(self):
        self.fake((["image", "inspect"], done(1)), (["build"], done(1, err="no network")))
        with self.assertRaisesRegex(container.ContainerError, "no network"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx",
                                  self.wheels())


class ConstraintsTests(unittest.TestCase):

    @staticmethod
    def runner(records="[]", freeze=None):
        """A fake ``run``: the record check (``-c``) answers ``records``, the
        freeze answers ``freeze``."""
        calls = []

        def run(argv, **_kw):
            calls.append(argv)
            if argv[1] == "-c":
                return records if isinstance(records, subprocess.CompletedProcess) \
                    else done(out=records)
            return freeze or done(out="a==1\r\nb==2\r\n")
        return run, calls

    def test_positive_the_freeze_is_written_with_lf_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "image" / "constraints.txt"
            run, calls = self.runner()
            container.write_constraints(Path("py"), dest, run=run)
            self.assertEqual(dest.read_bytes(), b"a==1\nb==2\n")
            self.assertEqual(calls[-1][1:], ["-m", "pip", "freeze", "--exclude-editable"])

    def test_rejection_a_failed_or_empty_freeze_raises(self):
        for answer in (done(1, err="bad"), done(0, out="  \n")):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as tmp:
                run, _calls = self.runner(freeze=answer)
                with self.assertRaises(container.ContainerError):
                    container.write_constraints(Path("py"), Path(tmp) / "c.txt", run=run)

    def test_rejection_a_package_with_two_records_is_refused_before_the_freeze(self):
        # The S4 live check's finding: pip freeze reports one record's version
        # while the installed code is the other's.
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "c.txt"
            run, calls = self.runner(records='["pypdf 6.13.2 and 6.14.2"]')
            with self.assertRaisesRegex(container.ContainerError,
                                        "two install records.*pypdf 6.13.2 and 6.14.2"):
                container.write_constraints(Path("py"), dest, run=run)
            self.assertEqual(len(calls), 1)
            self.assertFalse(dest.exists())

    def test_rejection_a_record_check_that_fails_or_is_unreadable_is_refused(self):
        # A failing exit with a valid-looking "[]" included: a check that did not
        # finish is not a statement that nothing is doubled (the ledger's rule).
        for answer in (done(1, err="no python"), done(1, out="[]"), done(0, out="not json"),
                       done(0, out="{}")):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as tmp:
                run, calls = self.runner(records=answer)
                with self.assertRaisesRegex(container.ContainerError, "could not list"):
                    container.write_constraints(Path("py"), Path(tmp) / "c.txt", run=run)
                self.assertEqual(len(calls), 1)

    def test_positive_the_record_check_finds_a_package_recorded_twice(self):
        # The check itself, run by a real interpreter over two invented records.
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0", "2.0"):
                write(Path(tmp) / f"inventedpkg-{version}.dist-info" / "METADATA",
                      f"Metadata-Version: 2.1\nName: inventedpkg\nVersion: {version}\n"
                      .encode())
            env = dict(os.environ, PYTHONPATH=tmp)
            out = subprocess.run([sys.executable, "-c", container._DOUBLE_RECORDS],
                                 capture_output=True, text=True, encoding="utf-8", env=env).stdout
            self.assertIn("inventedpkg 1.0 and 2.0", out)

    def test_negative_control_a_single_record_is_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / "inventedpkg-1.0.dist-info" / "METADATA",
                  b"Metadata-Version: 2.1\nName: inventedpkg\nVersion: 1.0\n")
            env = dict(os.environ, PYTHONPATH=tmp)
            out = subprocess.run([sys.executable, "-c", container._DOUBLE_RECORDS],
                                 capture_output=True, text=True, encoding="utf-8", env=env).stdout
            self.assertNotIn("inventedpkg", out)


# ---------------------------------------------------------------------------
# The clean copy
# ---------------------------------------------------------------------------
class CopyCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root = self.root = self.tmp / "project"
        write(root / "tracked.txt", b"committed\n")
        write(root / "sub" / "kept.txt", b"kept\n")
        write(root / "deleted.txt", b"gone soon\n")
        write(root / ".env.example", b"KEY=\n")
        write(root / ".gitignore", b"secret/\n*.log\n")
        git(self.tmp, "init", "-q", str(root))
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "Test")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "start", "--no-verify")
        self.head = git(root, "rev-parse", "HEAD").strip()
        # A later commit, so the start commit is not the clone's own HEAD and only
        # a real checkout of it can put the copy there.
        write(root / "later.txt", b"committed after the start\n")
        git(root, "add", "later.txt")
        git(root, "commit", "-q", "-m", "later", "--no-verify")
        # The live tree at the start of the run.
        write(root / "tracked.txt", b"edited live\n")
        (root / "deleted.txt").unlink()
        write(root / "new.txt", b"new and unignored\n")
        write(root / "secret" / "key.txt", b"ignored folder\n")
        write(root / "x.log", b"ignored file\n")
        write(root / ".env.local", b"KEY=real\n")
        write(root / "sub" / ".env", b"KEY=real\n")

    def copy(self, floor, n=1):
        dest = self.tmp / "copies" / f"copy-{n}"
        return container.make_copy(self.root, dest, self.head, floor)

    def files(self, dest):
        return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")
                      if p.is_file() and ".git" not in p.relative_to(dest).parts)


class StartIgnoredTests(CopyCase):

    def test_positive_every_ignored_path_and_every_untracked_env_file(self):
        self.assertEqual(container.start_ignored(self.root),
                         [".env.local", "secret/", "sub/.env", "x.log"])

    def test_negative_a_tracked_env_example_is_not_on_the_floor(self):
        self.assertNotIn(".env.example", container.start_ignored(self.root))

    def test_positive_the_list_round_trips_through_its_file(self):
        floor = container.start_ignored(self.root)
        path = container.write_start_ignored(floor, self.tmp / "frozen" / "start-ignored.txt")
        self.assertEqual(container.read_start_ignored(path), floor)
        self.assertEqual(path.read_bytes(), b".env.local\nsecret/\nsub/.env\nx.log\n")


class MakeCopyTests(CopyCase):

    def test_positive_live_tracked_and_unignored_files_at_the_start_commit(self):
        dest = self.copy(container.start_ignored(self.root))
        self.assertEqual(self.files(dest), [".env.example", ".gitignore", "later.txt",
                                            "new.txt", "sub/kept.txt", "tracked.txt"])
        self.assertEqual((dest / "tracked.txt").read_bytes(), b"edited live\n")
        self.assertEqual(git(dest, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(sorted(git(dest, "status", "--porcelain").splitlines()),
                         [" D deleted.txt", " M tracked.txt", "?? later.txt", "?? new.txt"])
        # Checked out exactly as committed, whatever the host's autocrlf.
        self.assertEqual(git(dest, "config", "core.autocrlf").strip(), "false")

    def test_positive_a_rule_removed_after_the_start_still_leaves_the_path_out(self):
        floor = container.start_ignored(self.root)
        write(self.root / ".gitignore", b"*.log\n")  # a builder drops "secret/"
        dest = self.copy(floor)
        self.assertFalse((dest / "secret").exists())
        self.assertEqual(subprocess.run(["git", "-C", str(dest), "check-ignore", "-q",
                                         "secret/key.txt"]).returncode, 0)

    def test_positive_every_floor_path_stays_ignored_in_the_copy(self):
        dest = self.copy(container.start_ignored(self.root))
        for rel in (".env.local", "secret/key.txt", "sub/.env", "x.log"):
            with self.subTest(rel=rel):
                self.assertEqual(subprocess.run(["git", "-C", str(dest), "check-ignore",
                                                 "-q", rel]).returncode, 0)

    def test_positive_a_rule_added_after_the_start_is_honoured(self):
        floor = container.start_ignored(self.root)
        write(self.root / ".gitignore", b"secret/\n*.log\nnew.txt\n")
        self.assertNotIn("new.txt", self.files(self.copy(floor)))

    def test_rejection_without_the_floor_an_unignored_env_file_would_be_copied(self):
        # The control for the floor: the untracked .env files are not ignored by
        # .gitignore, so only the floor keeps them out.
        self.assertIn(".env.local", self.files(self.copy([], n=2)))

    def test_positive_every_file_written_is_first_checked_for_a_link_in_the_copy(self):
        # Code review R12-3's second layer is used, not only defined.
        floor = container.start_ignored(self.root)
        with mock.patch.object(container, "_refuse_link_in_copy",
                               wraps=container._refuse_link_in_copy) as spy:
            dest = self.copy(floor)
        self.assertEqual(sorted(c.args[1] for c in spy.call_args_list),
                         sorted(self.files(dest)))

    def test_positive_each_file_is_checked_before_it_is_written(self):
        # Code review R13-2: the order, not only the count.
        events = []
        real_check, real_copy = container._refuse_link_in_copy, container.shutil.copyfile
        dest = self.tmp / "copies" / "copy-1"

        def check(d, rel):
            events.append(("check", rel))
            return real_check(d, rel)

        def copy(src, target, **kw):
            events.append(("write", Path(target).relative_to(dest).as_posix()))
            return real_copy(src, target, **kw)
        with mock.patch.object(container, "_refuse_link_in_copy", check), \
                mock.patch.object(container.shutil, "copyfile", copy):
            container.make_copy(self.root, dest, self.head, container.start_ignored(self.root))
        writes = [i for i, (kind, _rel) in enumerate(events) if kind == "write"]
        self.assertEqual(len(writes), len(self.files(dest)))
        for i in writes:
            self.assertEqual(events[i - 1], ("check", events[i][1]))

    def test_rejection_a_link_appearing_in_the_copy_is_refused_and_not_written_through(self):
        # Code review R13-2: a link put into the copy after its checkout, pointing at
        # a file outside, before any file is copied over it.
        outside = self.tmp / "outside.txt"
        write(outside, b"must not change\n")
        real_git, dest = container._git, self.tmp / "copies" / "copy-1"

        def git_then_link(cwd, *args):
            out = real_git(cwd, *args)
            if args[:1] == ("checkout",) and Path(cwd) == dest:
                (dest / "tracked.txt").unlink()
                os.symlink(outside, dest / "tracked.txt")
            return out
        with mock.patch.object(container, "_git", git_then_link):
            try:
                with self.assertRaisesRegex(container.ContainerError,
                                            "through a link in the copy: tracked.txt"):
                    container.make_copy(self.root, dest, self.head,
                                        container.start_ignored(self.root))
            except OSError as exc:
                self.skipTest(f"cannot create a symbolic link here: {exc}")
        self.assertEqual(outside.read_bytes(), b"must not change\n")

    def test_rejection_an_existing_destination_is_refused(self):
        dest = self.tmp / "copies" / "copy-1"
        dest.mkdir(parents=True)
        with self.assertRaisesRegex(container.ContainerError, "already exists"):
            self.copy([])

    def test_positive_floor_entries_are_escaped_anchored_exclude_lines(self):
        self.assertEqual(container._exclude_line("secret/"), "/secret/")
        self.assertEqual(container._exclude_line("a*b?[c].log"), "/a\\*b\\?\\[c\\].log")
        self.assertEqual(container._exclude_line("#x"), "/\\#x")
        self.assertEqual(container._exclude_line("!y"), "/\\!y")


class RefusalTests(CopyCase):

    def refused(self, needle):
        dest = self.tmp / "copies" / "copy-1"
        with self.assertRaisesRegex(container.ContainerError, needle):
            container.make_copy(self.root, dest, self.head, container.start_ignored(self.root))
        self.assertFalse(dest.exists())

    def symlink(self, link, target):
        try:
            os.symlink(target, link)
        except OSError as exc:
            self.skipTest(f"cannot create a symbolic link here: {exc}")

    def test_rejection_a_link_to_an_ignored_file(self):
        self.symlink(self.root / "to-secret.txt", self.root / "secret" / "key.txt")
        self.refused("link or junction: to-secret.txt")

    def test_rejection_a_link_to_a_file_outside_the_project(self):
        write(self.tmp / "outside.txt")
        self.symlink(self.root / "to-outside.txt", self.tmp / "outside.txt")
        self.refused("link or junction: to-outside.txt")

    @unittest.skipUnless(sys.platform == "win32", "junctions are a Windows feature")
    def test_rejection_a_junction(self):
        import _winapi
        write(self.tmp / "elsewhere" / "file.txt")
        _winapi.CreateJunction(str(self.tmp / "elsewhere"), str(self.root / "junction"))
        self.refused("link or junction: junction")

    def test_rejection_a_gitlink(self):
        git(self.root, "update-index", "--add", "--cacheinfo",
            f"160000,{self.head},module")
        self.refused("gitlink \\(submodule\\): module")


class CommittedLinkTests(unittest.TestCase):
    """Code review R12-3: a link in the start commit, replaced by an ordinary file in
    the live tree, must not be followed when the live file is written into the copy.
    Git here is given a global configuration that creates links on checkout
    (core.symlinks=true), so only the copy's own setting can keep the link out."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.outside = self.tmp / "outside.txt"
        write(self.outside, b"must not change\n")
        gitconfig = self.tmp / "gitconfig"
        write(gitconfig, b"[core]\n\tsymlinks = true\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(gitconfig)})
        patcher.start()
        self.addCleanup(patcher.stop)
        root = self.root = self.tmp / "project"
        write(root / "kept.txt")
        git(self.tmp, "init", "-q", str(root))
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "Test")
        try:
            os.symlink(self.outside, root / "link.txt")
        except OSError as exc:
            self.skipTest(f"cannot create a symbolic link here: {exc}")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "start with a link", "--no-verify")
        self.head = git(root, "rev-parse", "HEAD").strip()
        (root / "link.txt").unlink()
        write(root / "link.txt", b"now an ordinary file\n")

    def test_negative_control_this_git_would_check_the_link_out_as_a_link(self):
        plain = self.tmp / "plain-clone"
        git(self.tmp, "clone", "-q", str(self.root), str(plain))
        git(plain, "checkout", "-q", self.head)
        self.assertTrue(os.path.islink(plain / "link.txt"))

    def test_positive_the_live_file_lands_in_the_copy_and_the_outside_is_untouched(self):
        dest = container.make_copy(self.root, self.tmp / "copy", self.head, [])
        self.assertFalse(os.path.islink(dest / "link.txt"))
        self.assertEqual((dest / "link.txt").read_bytes(), b"now an ordinary file\n")
        self.assertEqual(self.outside.read_bytes(), b"must not change\n")
        self.assertEqual(git(dest, "config", "core.symlinks").strip(), "false")


class LinkInCopyTests(unittest.TestCase):
    """The second layer: a write into the copy refuses a path that is, or lies
    under, a link or junction inside the copy."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dest = self.tmp / "copy"
        write(self.dest / "plain" / "f.txt")
        write(self.tmp / "elsewhere" / "f.txt")

    def test_negative_control_an_ordinary_path_is_written(self):
        container._refuse_link_in_copy(self.dest, "plain/f.txt")
        container._refuse_link_in_copy(self.dest, "new/folder/g.txt")

    def test_rejection_a_link_at_the_path_or_above_it(self):
        try:
            os.symlink(self.tmp / "elsewhere" / "f.txt", self.dest / "file-link")
            os.symlink(self.tmp / "elsewhere", self.dest / "dir-link",
                       target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create a symbolic link here: {exc}")
        for rel, named in (("file-link", "file-link"), ("dir-link/f.txt", "dir-link")):
            with self.subTest(rel=rel):
                with self.assertRaisesRegex(container.ContainerError,
                                            f"through a link in the copy: {named}$"):
                    container._refuse_link_in_copy(self.dest, rel)

    @unittest.skipUnless(sys.platform == "win32", "junctions are a Windows feature")
    def test_rejection_a_junction_above_the_path(self):
        import _winapi
        _winapi.CreateJunction(str(self.tmp / "elsewhere"), str(self.dest / "junction"))
        with self.assertRaisesRegex(container.ContainerError, "through a link in the copy: junction$"):
            container._refuse_link_in_copy(self.dest, "junction/f.txt")


class CopyPlaceTests(unittest.TestCase):

    def test_positive_copies_live_under_the_temp_folder_not_the_frozen_one(self):
        self.assertEqual(container.copy_dir("20261007-120000-abcd", 3),
                         Path(tempfile.gettempdir()) / "book-dragon-copies"
                         / "20261007-120000-abcd" / "copy-3")

    def test_positive_remove_copy_removes_read_only_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "copy"
            write(dest / "obj")
            os.chmod(dest / "obj", 0o444)
            container.remove_copy(dest)
            self.assertFalse(dest.exists())

    def placed(self, tmp):
        return mock.patch.object(container, "copy_dir",
                                 lambda run_id, n: Path(tmp) / run_id / f"copy-{n}")

    def test_positive_next_copy_is_one_past_the_highest_left(self):
        # S6b: the loop's checks and the check helper share this numbering.
        with tempfile.TemporaryDirectory() as tmp, self.placed(tmp):
            self.assertEqual(container.next_copy("r"), Path(tmp) / "r" / "copy-1")
            for name in ("copy-1", "copy-3", "copy-x"):
                (Path(tmp) / "r" / name).mkdir(parents=True)
            self.assertEqual(container.next_copy("r"), Path(tmp) / "r" / "copy-4")

    def test_positive_remove_copies_removes_the_runs_whole_folder_only(self):
        # Plan 7.3: at a stop reason a resume cannot continue.
        with tempfile.TemporaryDirectory() as tmp, self.placed(tmp):
            write(Path(tmp) / "r" / "copy-2" / "obj")
            write(Path(tmp) / "other" / "copy-1" / "obj")
            container.remove_copies("r")
            container.remove_copies("never-made")  # nothing there is not an error
            self.assertFalse((Path(tmp) / "r").exists())
            self.assertTrue((Path(tmp) / "other" / "copy-1" / "obj").exists())


class DockerFailedTests(unittest.TestCase):
    """Code review R26-1: Docker's own failure inside `docker run` (exit 125 and a line
    of its own starting "docker: ") is told apart from a check's own exit."""

    def test_positive_dockers_own_error_is_docker_failing(self):
        for err in ("docker: Cannot connect to the Docker daemon at npipe:x.\n",
                    "Unable to find image 'x' locally\n"
                    "docker: Error response from daemon: pull access denied.\n"):
            with self.subTest(err=err):
                self.assertTrue(container.docker_failed(125, err))

    def test_negative_a_checks_own_exit_or_output_is_not(self):
        for code, err in ((125, "my check gave up\n"), (125, ""), (125, None),
                          (1, "docker: Cannot connect to the Docker daemon\n"),
                          (125, "the word docker: inside a line\n")):
            with self.subTest(code=code, err=err):
                self.assertFalse(container.docker_failed(code, err))


class CloneRetryTests(unittest.TestCase):
    """Decision 30, S6b: the copy's ``git clone`` retries git being refused permission
    to write one of its own object files, and fails at once on anything else."""

    REFUSED = done(128, err="error: unable to write file .git/objects/ab/cdef: "
                            "Permission denied\nfatal: cannot store pack file\n")

    def clone(self, answers):
        calls, slept = [], []
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "copy"

            def run(argv, **kwargs):
                calls.append(dest.exists())  # a failed attempt's clone is gone first
                write(dest / "partial")
                return answers.pop(0)
            result = container._clone(Path(tmp) / "project", dest, run=run,
                                      sleep=slept.append)
        return result, calls, slept

    def test_positive_a_refusal_that_clears_is_retried(self):
        result, calls, slept = self.clone([self.REFUSED, done(0)])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [False, False])
        # Decision 30's values written out, not read from the code (code review R29-3).
        self.assertEqual(slept, [0.5])

    def test_rejection_a_refusal_that_never_clears_fails_after_three_attempts(self):
        # More refusals queued than attempts allowed, so the count is the code's own.
        result, calls, slept = self.clone([self.REFUSED] * 6)
        self.assertEqual(result.returncode, 128)
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, [0.5, 0.5])

    def test_rejection_any_other_failure_fails_at_once(self):
        for err in ("fatal: repository not found", "error: Permission denied"):
            with self.subTest(err=err):
                result, calls, slept = self.clone([done(128, err=err), done(0)])
                self.assertEqual(result.returncode, 128)
                self.assertEqual((len(calls), slept), (1, []))


class PrepareImageTests(ImageCase):
    """Plan 7.1 item 4 and R24-1 (S6b): the run's image step. The constraints file goes
    to the given folder (decision 32); a dry run builds nothing and reports reuse,
    build, or unknown while Docker is not running, asking only its status then."""

    STATUS_RUNNING = DockerStateTests.STATUS_RUNNING
    STATUS_STOPPED = DockerStateTests.STATUS_STOPPED

    def prepare(self, **kwargs):
        work = self.tmp / "work"

        def constraints(python, dest):
            write(Path(dest), b"alpha==1.0\n")
            return Path(dest)
        with mock.patch.object(container, "write_constraints", constraints):
            return container.prepare_image(self.root, "py", work, **kwargs), work

    def test_rejection_a_dry_run_with_docker_stopped_reports_unknown(self):
        fake = self.fake((["desktop", "status"], self.STATUS_STOPPED))
        result, work = self.prepare(dry_run=True)
        self.assertEqual(result["would"], "unknown: Docker is not running")  # R29-3
        self.assertTrue(result["tag"].startswith("bookdragon-checks:"))
        self.assertEqual(fake.calls, [["desktop", "status"]])
        self.assertTrue((work / "constraints.txt").is_file())

    def test_positive_a_dry_run_reports_reuse_or_build_by_the_tag(self):
        for code, would in ((0, "reuse"), (1, "build")):
            with self.subTest(would=would):
                fake = self.fake((["desktop", "status"], self.STATUS_RUNNING),
                                 (["image", "inspect"], done(code)))
                result, work = self.prepare(dry_run=True)
                self.assertEqual(result["would"], would)
                # The whole command (code review R31-2): Docker is asked about the tag
                # the preview reports, which is the tag of these requirements and
                # this constraints file (TagTests prove the hash itself).
                self.assertEqual(result["tag"], container.image_tag(
                    self.root, container.requirements_files(self.root),
                    work / "constraints.txt"))
                self.assertEqual(fake.calls, [["desktop", "status"],
                                              ["image", "inspect", result["tag"]]])

    def test_positive_a_real_start_builds_from_the_folder_it_was_given(self):
        seen, filled = [], []

        def build(root, constraints, context, wheels):
            seen.append((Path(constraints), Path(context)))
            wheels()
            return "bookdragon-checks:abc", True

        def fill(constraints, store, python, tags):
            filled.append((Path(constraints), Path(store), python, tags))
            return [], []
        with mock.patch.object(container, "build_image", build), \
                mock.patch.object(container, "fill_wheel_store", fill), \
                mock.patch.object(container, "image_tags", return_value=["py3-none-any"]):
            result, work = self.prepare()
        self.assertEqual(result, {"tag": "bookdragon-checks:abc", "built": True,
                                  "constraints": work / "constraints.txt"})
        self.assertEqual(seen, [(work / "constraints.txt", work / "context")])
        # The store is filled from the same constraints, by the run's own Python, with
        # the tags the image itself gave.
        self.assertEqual(filled, [(work / "constraints.txt", container.wheel_store(), "py",
                                   ["py3-none-any"])])

    def test_negative_control_a_dry_run_does_not_ask_the_image_for_its_tags(self):
        fake = self.fake((["desktop", "status"], self.STATUS_RUNNING),
                         (["image", "inspect"], done(1)))
        with mock.patch.object(container, "image_tags", side_effect=AssertionError):
            self.prepare(dry_run=True)
        self.assertFalse([c for c in fake.calls if c[0] == "run"])


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
class RunTests(DockerCase):

    COPY = Path(tempfile.gettempdir()) / "book-dragon-copies" / "r" / "copy-1"

    def test_positive_the_exact_offline_command(self):
        fake = self.fake((["run"], done(3, "out", "err")))
        result = container.run_in_container(self.COPY, ["python", "-m", "x"], "img:1")
        self.assertEqual(result, (3, "out", "err"))
        self.assertEqual(fake.kwargs[0], {"raise_timeout": True})
        args = fake.calls[0]
        self.assertEqual(args[:2], ["run", "--rm"])
        self.assertEqual(args[2], "--name")
        self.assertRegex(args[3], r"^bookdragon-run-[0-9a-f]{12}$")
        self.assertEqual(args[4:], [
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "4g", "--pids-limit", "512", "-e", "BOOK_DRAGON_CLEAN_COPY=1",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{self.COPY.as_posix()}:/work",
            "img:1", "python", "-m", "x"])

    def test_rejection_a_command_that_is_not_a_list_of_strings_never_reaches_docker(self):
        fake = self.fake()
        for argv in ("python -m x", [], ["python", 1]):
            with self.subTest(argv=argv):
                with self.assertRaises(container.ContainerError):
                    container.run_in_container(self.COPY, argv, "img:1")
        self.assertEqual(fake.calls, [])

    def test_rejection_a_copy_path_with_a_space_or_quote_never_reaches_docker(self):
        fake = self.fake()
        for bad in ("C:/Temp/my copy", "C:/Temp/it's", 'C:/Temp/"q"'):
            with self.subTest(path=bad):
                with self.assertRaisesRegex(container.ContainerError, "space or a quote"):
                    container.run_in_container(Path(bad), ["python"], "img:1")
        self.assertEqual(fake.calls, [])

    @staticmethod
    def slow(args):
        raise subprocess.TimeoutExpired(args, 5)

    def timed_out(self, kill):
        fake = self.fake((["run"], self.slow), (["kill"], kill))
        with self.assertRaises(container.ContainerError) as caught:
            container.run_in_container(self.COPY, ["python"], "img:1", timeout=5)
        name = fake.calls[0][3]
        self.assertEqual(fake.calls[1], ["kill", name])
        return str(caught.exception), name

    def test_positive_a_run_past_its_time_is_killed_and_says_so(self):
        message, _name = self.timed_out(done(0))
        self.assertEqual(message, "the container command ran past 5 s and was stopped")

    def test_rejection_a_refused_or_unanswered_kill_is_not_called_stopped(self):
        # Code review R11-2: the container may still be running, and is named.
        def hung(_args):
            raise container.ContainerError("`docker kill` did not answer within 60 s")
        for kill in (done(1, err="no such container"), hung):
            with self.subTest(kill=kill):
                message, name = self.timed_out(kill)
                self.assertNotIn("was stopped", message)
                self.assertIn(f"container {name} may still be running", message)
                self.assertIn(f"docker kill {name}", message)


class FixtureHelperTests(unittest.TestCase):
    """The suite's own fixture helper (code review R21-1)."""

    def test_rejection_a_failing_git_command_names_gits_own_error(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with self.assertRaises(FixtureGitError) as caught:
            git(Path(folder.name), "add", "-A")
        self.assertIn("`git add -A` exited 128", str(caught.exception))
        self.assertIn("not a git repository", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
