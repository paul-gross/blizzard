# Plan review (advanced-development-workflow)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan.
Review it against the work item's intent and the project's conventions. Fold your improvement-tier findings into the
plan and publish it as `reviewed-plan`; bounce for must-fix findings alone.

## Start from what is there

Run `blizzard runner artifact list`: the newest `plan` asset is your subject
(`blizzard runner artifact get plan --content`), `plan-findings` an earlier round's record, `plan-finding-refutes` the
findings the plan node declined. With no `plan` asset, do not invent one: `blizzard runner ask`.

## Adjudicate refutations first

The newest `plan-finding-refutes` asset is the whole record — answer every entry, matched by **anchor**, never id (ids
restart at `F1`): an **`accepted`** entry stays accepted, carried into your `plan-findings` with its anchor; **accept**
an `open` entry whose argument holds; **reject** one whose argument does not — re-raise it and answer the argument.

## The gates

If the workspace declares a plan-review process, use it. Absent one, run two gates:

- **Verifiability** — every planned change maps to a verification method the project declares, or the plan first
  schedules building the missing one. An unjustified missing manual method is must-fix.
- **Architecture** — the plan conforms to the project's architecture guidance.

Check every owed surface (code, agent-facing context, public docs) is planned, and phases are ordered, coherent, and
independently verifiable.

## Steps no fleet worker can complete

A plan is never `acceptable` while a step or acceptance criterion cannot be completed by any fleet worker:

- an action only an operator can take: repository or forge settings, permissions, credentials, external accounts;
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

Publish `reviewed-plan` **before** the verdict — build implements it, not `plan` — with
`blizzard runner artifact create --name reviewed-plan` and the full plan on stdin:

- **`acceptable`** — the plan with your improvement-tier findings folded in: edit surgically (correct, delete, tighten —
  never expand or comment) and pipe it back in.
- **`must-fix`** — the plan verbatim:
  `blizzard runner artifact get plan --content | blizzard runner artifact create --name reviewed-plan`.

## Submit

Before you declare done, submit `plan-findings` (`blizzard runner artifact create --name plan-findings`): how you
adjudicated every refutation, and every finding carrying:

- **id** — `F1`, `F2`, …, within this submission only.
- **severity** — `blocking` for must-fix; on `acceptable`, `folded` if fixed in `reviewed-plan`, else `should-fix`.
- **anchor** — `<repo>/<path>:<line>`, `<repo>/<path>::<symbol>`, or `<asset-name>::<section>`.
- a **description** held to the docket's bound: one or two sentences, at most 300 characters — the defect and its
  consequence; a fact needed to act on it — a reproduction command, an expected/actual pair — rides a `detail:`
  continuation, at most two lines.

Restated from the docket; read it in full with `blizzard runner artifact get docket --scope graph --content`. If that
read fails or is empty, proceed on the restatement above.
