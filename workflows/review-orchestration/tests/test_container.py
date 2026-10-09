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


TRUST = "-----BEGIN CERTIFICATE-----\nVEVTVA==\n-----END CERTIFICATE-----\n"


class TagTests(ImageCase):

    def expected(self, constraints_bytes, trust):
        digest = hashlib.sha256()
        for rel in ("requirements.txt", "a/req.txt"):
            digest.update(rel.encode() + b"\0" + (self.root / rel).read_bytes() + b"\0")
        digest.update(constraints_bytes + b"\0" + trust.encode())
        return "bookdragon-checks:" + digest.hexdigest()[:12]

    def test_positive_the_tag_is_the_hash_of_the_files_constraints_and_trust(self):
        files = [self.root / "requirements.txt", self.root / "a" / "req.txt"]
        self.assertEqual(container.image_tag(self.root, files, self.constraints, TRUST),
                         self.expected(self.constraints.read_bytes(), TRUST))

    def test_positive_any_input_changing_changes_the_tag(self):
        files = [self.root / "requirements.txt", self.root / "a" / "req.txt"]
        before = container.image_tag(self.root, files, self.constraints, TRUST)
        self.assertNotEqual(before, container.image_tag(self.root, files[::-1],
                                                        self.constraints, TRUST))
        self.assertNotEqual(before, container.image_tag(self.root, files,
                                                        self.constraints, TRUST + TRUST))
        write(self.constraints, b"alpha==1.1\n")
        self.assertNotEqual(before, container.image_tag(self.root, files,
                                                        self.constraints, TRUST))


class HostTrustTests(unittest.TestCase):
    """Decision 26: the image build trusts the roots the host trusts, so an HTTPS
    scanner's root (which re-signs every site on the build machine) is trusted
    while every certificate is still verified."""

    def test_rejection_a_root_not_trusted_for_tls_servers_is_left_out(self):
        # Code review R14-1: Windows trusts some roots only for other purposes.
        server, everything, signing = b"\x01server", b"\x02all", b"\x03signing"
        store = [(server, "x509_asn", {"1.3.6.1.5.5.7.3.3", "1.3.6.1.5.5.7.3.1"}),
                 (everything, "x509_asn", True),
                 (signing, "x509_asn", {"1.3.6.1.5.5.7.3.3"}),
                 (b"\x04nothing", "x509_asn", set())]
        with mock.patch.object(container.ssl, "enum_certificates", create=True,
                               return_value=store):
            bundle = container.host_trust_bundle()
        pem = lambda d: container.ssl.DER_cert_to_PEM_cert(d).replace("\r\n", "\n")
        self.assertEqual(bundle, "".join(sorted([pem(server), pem(everything)])))
        self.assertNotIn(pem(signing), bundle)

    def test_positive_windows_roots_as_sorted_unique_pem(self):
        der_b, der_a = b"\x02second", b"\x01first"
        store = [(der_b, "x509_asn", True), (der_a, "x509_asn", True),
                 (der_a, "x509_asn", True), (b"\x03ignored", "pkcs_7_asn", True)]
        with mock.patch.object(container.ssl, "enum_certificates", create=True,
                               return_value=store):
            bundle = container.host_trust_bundle()
        expected = "".join(sorted(container.ssl.DER_cert_to_PEM_cert(d).replace("\r\n", "\n")
                                  for d in (der_a, der_b)))
        self.assertEqual(bundle, expected)
        self.assertEqual(bundle.count("BEGIN CERTIFICATE"), 2)

    def test_positive_elsewhere_the_default_certificate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cafile = Path(tmp) / "ca.pem"
            write(cafile, (TRUST + "not a certificate\n" + TRUST).encode())
            fake_ssl = mock.Mock(spec=["get_default_verify_paths"])
            fake_ssl.get_default_verify_paths.return_value = mock.Mock(cafile=str(cafile))
            with mock.patch.object(container, "ssl", fake_ssl):
                self.assertEqual(container.host_trust_bundle(), TRUST)

    def test_positive_this_machine_trusts_at_least_one_root(self):
        self.assertIn("-----BEGIN CERTIFICATE-----", container.host_trust_bundle())


class BuildTests(ImageCase):

    FREEZE_OK = done(out="alpha==1.0\nbeta==2.0\nsdk==1.1\nsetuptools==80\n")

    def build(self, *rules):
        fake = self.fake((["image", "inspect"], done(1)), *rules)
        ctx = self.tmp / "ctx"
        return fake, ctx, container.build_image(self.root, self.constraints, ctx, trust=TRUST)

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
        self.assertIn("RUN pip install --no-cache-dir -r requirements.txt -r a/req.txt "
                      "-r a/sub.txt -r b/req.txt -r workflows/review-orchestration/"
                      "requirements.txt -c constraints.txt\n", dockerfile)
        self.assertNotIn("-r constraints.txt", dockerfile)
        self.assertEqual(sorted(p.relative_to(ctx).as_posix() for p in ctx.rglob("*")
                                if p.is_file()),
                         ["Dockerfile", "a/req.txt", "a/sub.txt", "b/req.txt",
                          "constraints.txt", "host-roots.crt", "requirements.txt",
                          "workflows/review-orchestration/requirements.txt"])

    def test_positive_the_host_roots_are_trusted_before_anything_is_downloaded(self):
        _fake, ctx, _result = self.build(
            (["run", "--rm", "--network", "none"], self.FREEZE_OK))
        self.assertEqual((ctx / "host-roots.crt").read_text(encoding="utf-8"), TRUST)
        lines = (ctx / "Dockerfile").read_text(encoding="utf-8").splitlines()
        append = lines.index("RUN cat /req/host-roots.crt >> /etc/ssl/certs/ca-certificates.crt")
        pip_cert = lines.index("ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt")
        apt = next(i for i, l in enumerate(lines) if l.startswith("RUN apt-get"))
        pip = next(i for i, l in enumerate(lines) if l.startswith("RUN pip install"))
        self.assertLess(append, apt)
        self.assertLess(append, pip)
        self.assertLess(pip_cert, pip)
        self.assertIn("USER checker", lines)
        self.assertEqual(lines[0], "FROM python:3.13-slim")

    def test_negative_control_an_existing_tag_is_not_rebuilt(self):
        fake = self.fake((["image", "inspect"], done(0)))
        tag, built = container.build_image(self.root, self.constraints, self.tmp / "ctx",
                                           trust=TRUST)
        self.assertIs(built, False)
        self.assertEqual([c[0] for c in fake.calls], ["image"])
        self.assertFalse((self.tmp / "ctx").exists())

    def test_rejection_a_version_that_differs_fails_the_build_and_removes_the_image(self):
        fake = self.fake((["image", "inspect"], done(1)),
                         (["run", "--rm", "--network", "none"],
                          done(out="alpha==1.0\nbeta==2.1\n")))
        with self.assertRaisesRegex(container.ContainerError, "beta 2.0 != 2.1"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx")
        self.assertEqual(fake.calls[-1][:3], ["image", "rm", "-f"])
        self.assertTrue(fake.calls[-1][3].endswith("-unverified"))
        self.assertEqual([c for c in fake.calls if c[0] == "tag"], [])

    def failed_check(self, freeze, remove):
        fake = self.fake((["image", "inspect"], done(1)),
                         (["run", "--rm", "--network", "none"], freeze),
                         (["image", "rm", "-f"], remove))
        ctx = Path(tempfile.mkdtemp(dir=self.tmp)) / "ctx"  # a fresh folder each call
        with self.assertRaises(container.ContainerError) as caught:
            container.build_image(self.root, self.constraints, ctx, trust=TRUST)
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
                    container.build_image(self.root, self.constraints, ctx)
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
                                      trust=TRUST)
        self.assertEqual(len([c for c in fake.calls if c[0] == "build"]), 2)
        self.assertEqual([c for c in fake.calls if c[0] == "tag"], [])
        self.assertTrue(all(i.endswith("-unverified") for i in images))

    def test_rejection_a_checked_image_that_cannot_be_tagged_is_refused(self):
        self.fake((["image", "inspect"], done(1)),
                  (["run", "--rm", "--network", "none"], self.FREEZE_OK),
                  (["tag"], done(1, err="no space")))
        with self.assertRaisesRegex(container.ContainerError, "could not be tagged.*no space"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx", trust=TRUST)

    def test_negative_a_windows_only_constraint_absent_from_the_image_is_fine(self):
        # pywin32 is in the constraints only; versions are compared where both name it.
        _fake, _ctx, (_tag, built) = self.build(
            (["run", "--rm", "--network", "none"], self.FREEZE_OK))
        self.assertIs(built, True)

    def test_rejection_a_failed_build_raises(self):
        self.fake((["image", "inspect"], done(1)), (["build"], done(1, err="no network")))
        with self.assertRaisesRegex(container.ContainerError, "no network"):
            container.build_image(self.root, self.constraints, self.tmp / "ctx")


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
