Review round $round has finished. The findings below are open. Mechanical findings
come from checks the orchestrator ran itself; the others from the reviewer. The
reviewer's reasoning about you is deliberately not included, only the findings and
their evidence.

For each finding, do exactly one of these:
  - fix it, within the same edit paths and maintenance rules as before: action "fixed";
  - decline it because the fix needs a file outside your edit paths: action
    "declined-scope". This ends the run so the user can widen the brief;
  - decline it because you believe it is wrong: action "declined-merits", with the
    reason in the note. If the reviewer raises it again, the run ends for the user
    to rule.

$tool_rules
THE OPEN FINDINGS
$findings

End your reply with one fenced json block listing every finding above exactly once,
and nothing else in the block, like this:

```json
[
  {"label": "R$round-1", "action": "fixed", "note": "what changed"}
]
```

The action must be "fixed", "declined-scope" or "declined-merits". Only the last json
block in your reply is read.
