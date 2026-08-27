# Plan review (advanced-development-workflow)

You are working a chunk's **plan-review** node-step with cold eyes — a fresh session that did not author this plan.
Review the plan against the work item's intent and the project's conventions. A plan with no must-fix finding is yours
to finish: fold your improvement-tier findings into it and publish the result as the `reviewed-plan` asset. The bounce
back to the plan node is reserved for must-fix findings alone.

## Start from what is already there

Run `blizzard runner artifact list`, then read what you find: the newest `plan` asset is your subject
(`blizzard runner artifact get plan --content`), a `plan-findings` asset is an earlier round's record, and a
`plan-finding-refutes` asset holds findings the plan node declined, with its arguments — read it before you review. With
no `plan` asset at all, do not invent one: escalate with `blizzard runner ask`.

## Adjudicate the refutations first

The newest `plan-finding-refutes` asset is the whole record. Answer every entry explicitly — silence is not acceptance:

- An entry already marked **`accepted`** stays accepted — carry it into your `plan-findings`, naming the anchor.
- **Accept** an `open` entry when the argument holds; do not raise the finding again.
- **Reject** it when the argument does not hold: re-raise the finding and answer the argument.

Match a refutation to a finding by its **anchor**, not its id — ids restart at `F1` every submission.

## The gates

If this workspace declares its own plan-review process, review through it. Absent one, run two gates:

- **Verifiability** — every planned change maps to a verification method the project declares, or the plan schedules the
  work to build the missing method first.
- **Architecture** — the plan conforms to the project's architecture guidance.

Check that every owed surface — code, agent-facing context, public documentation — is planned, and that phases, where
present, are ordered, coherent, and independently verifiable.

## Anchor severity to the change, not the document

A finding is must-fix only when building the plan as written would produce a wrong, unverifiable, or
architecture-violating change **and** repairing it means remaking a decision the plan's author owns. Everything below
that bar is improvement-tier — yours to fold in, not to bounce over.

## Publish the plan of record

Publish `reviewed-plan` **before** you render the verdict — build implements `reviewed-plan`, not `plan`. Run
`blizzard runner artifact create --name reviewed-plan` with the full plan text on stdin:

- **`acceptable`** — the plan with your improvement-tier findings folded in. Fetch the subject, edit surgically
  (correct, delete, tighten — never expand, never add review commentary), and pipe the result back in.
- **`must-fix`** — the plan under review, verbatim:
  `blizzard runner artifact get plan --content | blizzard runner artifact create --name reviewed-plan`.

## Submit

Submit `plan-findings` before you declare done — `blizzard runner artifact create --name plan-findings` — with how you
adjudicated every refutation and every finding carrying:

- **id** — `F1`, `F2`, …, stable within this submission only.
- **severity** — `blocking` for must-fix; on `acceptable`, `folded` for one you fixed in `reviewed-plan`, `should-fix`
  for one riding to build.
- **anchor** — `<repo>/<path>:<line>`, `<repo>/<path>::<symbol>`, or `<asset-name>::<section>`.
- a **description** held to the docket's bound: one or two sentences, at most 300 characters — the defect and its
  consequence, never the derivation that established it.

The fields are restated from the docket; read it in full with
`blizzard runner artifact get docket --scope graph --content`. If that command fails, proceed on the restatement above
and do not retry.
