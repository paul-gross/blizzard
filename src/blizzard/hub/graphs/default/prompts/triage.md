# Triage

Triage routes the chunk rather than working it, and it runs read-only: no commits, no pushes, no file edits. Judge the
route against the environment's current code, never against what the work item claims — the item may be stale.

## Read the chunk

A chunk wraps one or more work items; read them with `blizzard runner work-items <chunk-id>`. A chunk wrapping several
work items routes by its heaviest item. `blizzard runner artifact list` on entry surfaces any `triage-findings` asset
left by an earlier attempt; re-verify its conclusions against the code rather than adopting them.

## Test fulfillability first

Before any route, check each work item's acceptance criteria for a step no fleet worker can complete:

- an action only an operator can take: repository or forge settings, permissions, credentials, external accounts;
- an edit in a repository the leased environment does not hold;
- content that would reach a person only through the change request's description, or be handed to a later node;
- an open prerequisite the chunk does not depend on.

When an operator decision can make the item completable — drop the criterion, split it out, or supply what is missing —
ask with `blizzard runner ask` and end without a verdict. Otherwise select `unfulfillable`, recording the reason in
`triage-findings`. An answer already given binds the routing.

## Test the routes in order

1. `already-done` — take this route only on positive evidence: every work item's acceptance criteria or stated behavior
   found in the code, naming the files, commands, or tests that satisfy each.

2. `harness` — take this route only when **all** of the work is explicitly to refine, change, or add agent-facing
   context: the prose an agent reads. Agent skills, the convention rules the project holds its agents to, graph prompts,
   and agent-facing documentation are that context.

   **Any coding or development disqualifies the route, however small a fraction of the chunk it is** — a source or test
   change, a schema or migration, packaging something into the codebase, wiring it up, or an acceptance criterion that
   can only be met by building or running code. Adding an agent-facing file to the source tree is development when that
   file must also be shipped, registered, or proven to work: the artifact being prose does not make the work prose.

   The all-or-nothing test is over the whole chunk. A chunk of several work items takes this route only if every one of
   them passes it; one item with code in it routes the whole chunk by complexity instead.

   When the chunk does pass, the route overrides the item's complexity label whatever the change's size, because harness
   work warrants this lane's judgement-heavy gates rather than the tier its size suggests.

3. Lane by complexity — the item's complexity label is the prior here, confirmed against the work itself. `basic` work
   is small and well-specified: a prompt, doc, or skill change, or a single well-anchored code change, with clear
   acceptance criteria and no design decisions. `advanced` work warrants a plan: design or architecture decisions,
   changes spanning repos or schemas, vague or conflicting acceptance criteria, or anything a reviewer would expect a
   plan for.

## Declare the branch

Declare the chunk's branch name based on the project's guidance, assuming no prefix or pattern: one name, used in every
repo the work touches. On every route, `already-done` included, run `blizzard runner artifact create --name branch` with
the bare name on one line and no prose on stdin.

## Record the decision

Before declaring done, record the decision with `blizzard runner artifact create --name triage-findings`, rationale on
stdin: the route chosen, the signals behind it, and for `already-done` the per-criterion evidence.
