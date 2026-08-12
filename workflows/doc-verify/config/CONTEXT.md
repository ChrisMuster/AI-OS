# Doc-Verify Config

**Last modified:** 2026-08-12

## Purpose

Holds the configuration for doc-verify's opt-in sequence sweep. The sweep inventories a class of statement inside a document, and the class differs per project: a stage-order statement belongs to one plan's vocabulary, not to a general markdown checker. Keeping the vocabulary here rather than in `scripts/run.py` is what lets the workflow stay document-agnostic, which its Purpose depends on.

A config carries three things: the **subject** terms (what the statement is about), the **relation** terms (what it asserts between them), and the **known members** the sweep must be able to find. The third is the load-bearing one. A sweep is only as complete as its vocabulary, a vocabulary written by hand is a sample, and a sample reports a smaller number that reads exactly like a finished inventory. Declaring the members already known to be in the class turns "the vocabulary was checked" from a claim into a FAIL condition.

## Contents

- `sequence-example.json` - `workflows/doc-verify/config/sequence-example.json` - Worked example of a sweep config, carrying the stage-order vocabulary it was first built for. Copy it and replace the vocabularies for a different statement class.

## Inputs

None. These are inputs to the checker, not a workflow that consumes anything itself.

## Outputs

None. Nothing here is written by a script; a config is authored by hand.

## Steps

N/A. This is a configuration directory, not a workflow.

## Dependencies

- `workflows/doc-verify/scripts/` [[workflows/doc-verify/scripts/CONTEXT]] - `load_sequence_config` reads these files and rejects one with an empty `subject` or `relation`. The JSON shape is defined by that function, so a change there is a change to what belongs here.

## Known Issues

- A config is only as good as its `known_members` list, and that list is written by the same person whose vocabulary is being checked. It catches a vocabulary that cannot reach a member somebody already identified; it cannot catch a member nobody has identified yet. The control raises the floor rather than proving completeness, and it should not be quoted as though it did.
- `subject` and `relation` are regex alternatives, so a malformed pattern raises at compile time rather than being reported as a finding. That is deliberate - a broken pattern must not degrade into a quiet zero-candidate result - but it means an authoring error surfaces as a traceback.

## Revision History

- 2026-08-06 - Initial creation, alongside doc-verify's opt-in sequence sweep. Holds the worked-example config carrying the subject and relation vocabularies and the known-members positive control.
- 2026-08-12 - Corrected a mistranscribed known member in `sequence-example.json`. The first entry read "must not start until it has landed" where the document it controls says "must not start until 4a has landed", so the positive control had been failing and every run of the sweep reported FAIL with the message that its vocabulary has a hole. The vocabulary never had one: the sentence was being found as a candidate throughout, and the candidate count is unchanged at 61 either side of the fix. This is the failure mode the Known Issues bullet above describes from the other direction - the control is written by hand, so it can be wrong about the document rather than about the vocabulary, and a control that fails for its own reasons trains a reader to discount the one signal it exists to give.
