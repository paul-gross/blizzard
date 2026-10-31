# Plan review (advanced-development-workflow)

You work a chunk's **plan-review** node-step with cold eyes, in a fresh session that did not author the plan. Review it
against the work item's intent and the project's conventions. Fold improvement-tier findings into the plan and publish
it as `reviewed-plan`; bounce for must-fix alone.

## Start from what is there

Run `blizzard runner artifact list`: the newest `plan` asset is your subject
(`blizzard runner artifact get plan --content`), `plan-findings` an earlier round's record, `plan-finding-refutes` the
plan node's declined findings. With no `plan` asset, invent none: `blizzard runner ask`.

## Adjudicate refutations first

The newest `plan-finding-refutes` asset is the whole record — answer every entry, matched by **anchor**, never id (ids
restart at `F1`): an **`accepted`** entry stays accepted, carried into `plan-findings` with its anchor; **accept** an
`open` entry whose argument holds; **reject** one whose argument fails — re-raise it and answer it.

## The gates

Before judging, open the documents the plan lists as planned against, and the project's architecture guidance and
verifiability matrix, down to the rules binding this change; judge from what you read, not memory. Run the workspace's
plan-review process if it declares one; else two gates:

- **Verifiability** — every planned change maps to a declared verification method, or the plan first schedules building
  it. An unjustified missing manual method is must-fix.
- **Architecture** — the plan conforms to the project's architecture guidance.

Check every owed surface (code, agent-facing context, public docs) is planned, and phases are ordered, coherent, and
independently verifiable.

## Steps no fleet worker can complete

A plan is never `acceptable` while any step or acceptance criterion is beyond every fleet worker:

- an operator-only action: repository or forge settings, permissions, credentials, external accounts;
- an edit in a repository the leased environment does not hold;
- content that would reach a person only through the change request's description, or be handed to a later node;
- an open prerequisite the chunk does not depend on.

Rewording or deferring such a step is no fix. If an operator decision can make it completable, `blizzard runner ask` and
end without a verdict; otherwise select `unfulfillable`. A given answer binds routing.

## Anchor severity

A finding is must-fix only when building the plan as written would produce a wrong, unverifiable, or
architecture-violating change **and** repairing it means remaking a decision the plan's author owns. Anything less is
improvement-tier.

## Publish the plan of record

Publish `reviewed-plan` **before** the verdict — build implements it, not `plan` — piping the full plan to
`blizzard runner artifact create --name reviewed-plan`:

- **`acceptable`** — the plan with your findings folded in, edited surgically (correct, delete, tighten — never expand
  or comment).
- **`must-fix`** — the plan verbatim:
  `blizzard runner artifact get plan --content | blizzard runner artifact create --name reviewed-plan`.

## Submit

Before declaring done, submit `plan-findings` (`blizzard runner artifact create --name plan-findings`): how you
adjudicated every refutation, the path of each criteria document read per gate, and every finding carrying:

- **id** — `F1`, `F2`, …, within a submission.
- **severity** — `blocking` for must-fix; on `acceptable`, `folded` if fixed in `reviewed-plan`, else `should-fix`.
- **anchor** — `<repo>/<path>:<line>`, `<repo>/<path>::<symbol>`, or `<asset-name>::<section>`.
- a **description** — at most 300 characters: the defect and its consequence; a fact needed to act on it (a repro,
  expected/actual) rides a `detail:` continuation of at most two lines.

The docket holds the full shape: `blizzard runner artifact get docket --scope graph --content`, or proceed on this if it
is unreadable or empty.
