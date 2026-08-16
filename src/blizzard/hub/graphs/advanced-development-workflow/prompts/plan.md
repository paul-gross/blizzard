# Plan (advanced-development-workflow)

You are working a chunk's **plan** node-step. The chunk wraps one or more work items — read them with
`blizzard runner work-items <chunk-id>` — and your job is an implementation plan for the leased environment(s). Do not
write feature code in this node.

## Start from what is already there

You may be entering for the first time, retrying after a crash, or re-entering after the plan-review gate bounced the
plan. Run `blizzard runner artifact list` before assuming:

- A `plan` asset exists — read it (`blizzard runner artifact get plan --content`) and revise it. Do not start over.
- A `plan-findings` asset arrived with you — it names what to fix.
- Neither exists — this is a first draft.

## What the plan must contain

- **The feature branch**, named at the top: `feat/<slug>`, a short kebab-case description of the change (for example
  `feat/runner-crash-resume`). Every repo the build touches uses this one branch. Its name reaches the merge messages
  and the delivery PR, so it describes what the change is, never the environment it was built in.
- **How each change is verified** — every planned change maps to a verification method the project already declares;
  where none covers it, the plan schedules building that method first.
- **Conformance to the project's architecture guidance.**
- **Every surface the change owes** — code, agent-facing context, public documentation — each as planned work.
- **Acceptance criteria** referencing those verification methods.

Size the plan to the work: a small change earns a small plan, and only work needing ordered, independently verifiable
increments earns numbered **phases**. If this workspace declares its own planning process, author through it — its
conventions govern, including how it sizes a plan to the work.

Keep drafts and notes somewhere disposable: outside every repository working tree *and* outside the workspace directory
the fleet spawned you in — both are git working trees, and nothing sweeps a loose file in either. A per-chunk directory
under the machine's temporary space works; name it with `$BLIZZARD_CHUNK_ID`. Prefer the workspace's own scratch
location if it declares one.

## Submit

Before declaring done:

- The plan: run `blizzard runner artifact create --name plan` with the content on stdin.
- The refutation channel: run `blizzard runner artifact create --name plan-finding-refutes` with the content on stdin.
  Your newest submission is the entire record — reads resolve to it, so a later submission replaces the earlier one
  rather than adding to it, and the gate never looks for an older one. Read your own previous submission first
  (`blizzard runner artifact get plan-finding-refutes --content`) and restate **every refutation still standing**,
  including any the gate already accepted, each marked `open` or `accepted`. Write "nothing to refute" only when nothing
  is standing — on a first draft, or once every refuted finding is resolved or withdrawn.
