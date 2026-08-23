# Triage

Triage routes the chunk rather than working it, and it runs read-only: no commits, no pushes, no file edits. Judge the
route against the environment's current code, never against what the work item claims — the item may be stale.

## Read the chunk

A chunk wraps one or more work items; read them with `blizzard runner work-items <chunk-id>`. A chunk wrapping several
work items routes by its heaviest item. `blizzard runner artifact list` on entry surfaces any `triage-findings` asset
left by an earlier attempt; re-verify its conclusions against the code rather than adopting them.

## Test the routes in order

1. `already-done` — take this route only on positive evidence: every work item's acceptance criteria or stated behavior
   found in the code, naming the files, commands, or tests that satisfy each.

2. `harness` — take this route when the change's own subject, not code it touches in passing, is an agent-capability
   surface: an agent skill, a convention rule the project holds its agents to, a graph prompt or definition, or
   agent-facing documentation. It overrides the item's complexity label whatever the change's size, because harness work
   warrants the frontier tier rather than the tier its size suggests.

3. Lane by complexity — the item's complexity label is the prior here, confirmed against the work itself. `basic` work
   is small and well-specified: a prompt, doc, or skill change, or a single well-anchored code change, with clear
   acceptance criteria and no design decisions. `advanced` work warrants a plan: design or architecture decisions,
   changes spanning repos or schemas, vague or conflicting acceptance criteria, or anything a reviewer would expect a
   plan for.

## Record the decision

Before declaring done, record the decision with `blizzard runner artifact create --name triage-findings`, rationale on
stdin: the route chosen, the signals behind it, and for `already-done` the per-criterion evidence.
