# Sync Architecture - Tests

**Last modified:** 2026-08-26

## Purpose

Tests for `scripts/allowlist.py`, the shared selection rule, and the executable form
of its verification protocol. Every check carries three controls, named as such in the
test names:

- **Positive control** - a legal instance of the subject, which a counting check must
  find and a validating check must pass silently.
- **Rejection control** - an illegal instance, which the check must refuse. It proves
  the refusal path fires and says nothing about whether the definition is right.
- **Negative control** - something that is not an instance of the subject at all, so a
  clean result is known not to be silence from over-matching.

**This suite is deliberately not hermetic.** The doc-verify suite can be, because it
processes text. This module's whole subject is the difference between two git contexts,
so a test that stubbed git would be testing the stub. Every fixture is a real repository
built under `tempfile`: deterministic, no network, nothing written outside the temporary
directory, and the working directory restored even when a test fails.

The load-bearing control is `ShadowSelectionTests.test_positive_control_shadow_context_exposes_it`.
The defect the module was written to remove was a check running in a different git
context from the build it checked, whose symptom was an assertion that could not fail. A
suite run only against a clean tree cannot tell "the two contexts agree" from "I am not
really asking two different questions", so the fixture constructs the one state where a
correct implementation must return two different answers: a file that is both publicly
tracked and matched by an ignore rule.

## Contents

- test_allowlist.py - `workflows/sync-architecture/tests/test_allowlist.py` [[workflows/sync-architecture/tests/CONTEXT]] - 29 tests in six classes. `ExtractBlockTests` (counting: pathspecs returned, comments and blanks stripped, and rejection controls for a missing block, an unterminated fence, and an empty block, which is refused rather than returning an empty list because a check resolving zero pathspecs would report an empty selection as clean; negative control that a differently-labelled fence is not the block). `TrackedAndIgnoredTests` (counting: the invariant, whose positive control doubles as a regression control on `--no-index`, without which git refuses to report a tracked path as ignored and the function returns an empty set for a tree in any state). `ShadowSelectionTests` (counting: public context hides the tracked-and-ignored file, shadow context exposes it, and negative controls that selection is neither "everything" nor blind to the pathspecs). `SelectTests` (validating: the corrected set, the overlap reported rather than swallowed, and the mechanism pinned as the subtraction rather than as a property of how the pathspecs resolve). `StageTests` (the writing path: files become tracked, a bare probe repository accepts the add, a multi-batch stage adds every file, dry-run writes nothing, staging is idempotent, a failure returns False *and* reports git's reason, and the public working tree is untouched). `SmokeTests` (the real CLI as a subprocess: the self-test passes, `--list` reports a selection, and `--stage` refuses both a missing `--git-dir` and a missing repository).

## Inputs

- `scripts/allowlist.py`, imported directly by file path rather than by package import, so the suite does not depend on how the entry point sets up `sys.path`.
- A working `git`. Unlike most suites in this project, git is a genuine dependency rather than an incidental one: the subject under test is git behaviour.

## Outputs

- Test results on stdout (unittest). Exit code 0 when all pass, non-zero on failure. No files written outside the temporary directory.

## Steps

1. Run `python workflows/sync-architecture/tests/test_allowlist.py`.
2. The close-out verifier discovers this suite automatically, under the `sync-architecture` owner, by finding this tests directory and reading the files in it whose names begin with "test_" and end in ".py".
3. Append LOG.md with a completion or failure entry.

## Dependencies

- `workflows/sync-architecture/scripts/allowlist.py` [[workflows/sync-architecture/scripts/CONTEXT]], the module under test. Renaming or moving it breaks the file-path import here.
- `workflows/close-out/` [[workflows/close-out/CONTEXT]], which discovers and runs this suite as one of its three gates.
- `git`, on PATH.

## Known Issues

- The suite shells out to `git` for every fixture, so it is slower than a hermetic one, roughly twenty seconds. That is the cost of testing real git behaviour rather than a stub, and it is the right trade for this subject.
- `SmokeTests` runs the CLI against the real project rather than a fixture, so its assertions are deliberately weak: that a selection was reported, not what was in it. Anything stronger would encode the current contents of the working tree into a test.
- The batching test rebinds `allowlist._CHUNK_CHARS` to force multiple batches and restores it in a `finally`. That reaches into a module private, which is accepted here because the alternative is generating enough files to cross the real limit.

## Revision History

- 2026-08-26 - Initial creation, alongside `scripts/allowlist.py`. Added so the close-out verifier actually runs the module's checks: the module shipped with a `--self-test`, but close-out only discovers suites inside a workflow's own tests directory, so a full close-out reported "0 test file(s) across 0 suite(s)" and the self-test would never have run. The suite was mutation-tested on the way in, by removing the subtraction, removing `--no-index`, and removing the stderr reporting, and it catches all three. A fourth mutation caught a false claim rather than a defect: removing a `core.bare=false` argument changed nothing, which showed the argument had been added on a wrong diagnosis and documented as a fix. It was removed and the documentation corrected.
