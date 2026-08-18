# Plan (advanced-development-workflow)

You are working a chunk's **plan** node-step. The chunk wraps one or more work items — read them with
`blizzard runner work-items <chunk-id>` — and your job is an implementation plan for the leased environment(s). Do not
write feature code in this node.

## Start from what is already there

Run `blizzard runner artifact list` first: an existing `plan` asset is yours to revise
(`blizzard runner artifact get plan --content`) — do not start over; a `plan-findings` asset names what to fix; neither
means a first draft.

## What the plan must contain

- **The feature branch**, named at the top: `feat/<slug>`, a short kebab-case description of the change. Every repo the
  build touches uses this one branch.
- **How each change is verified** — every planned change maps to a verification method the project already declares;
  where none covers it, the plan schedules building that method first.
- **Conformance to the project's architecture guidance.**
- **Every surface the change owes** — code, agent-facing context, public documentation — each as planned work.
- **Acceptance criteria** referencing those verification methods.

Size the plan to the work: a small change earns a small plan, and only work needing ordered, independently verifiable
increments earns numbered **phases**. If this workspace declares its own planning process, author through it.

## Submit

Before declaring done:

- The plan: run `blizzard runner artifact create --name plan` with the content on stdin.
- The refutation channel: run `blizzard runner artifact create --name plan-finding-refutes` with the content on stdin.
  Your newest submission is the entire record, so restate every refutation still standing — not just this round's. The
  full docket this restates is retrievable directly: `blizzard runner artifact get docket --scope graph --content`; if
  that command fails, proceed on the restatement above and do not retry.
