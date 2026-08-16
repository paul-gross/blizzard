# Plan (advanced-development-workflow)

You are working a chunk's **plan** node-step. The chunk wraps one or more work items; read them with
`blizzard runner work-items <chunk-id>`. Author an implementation plan for the leased environment(s). Do not write
feature code in this node.

## Start from what is already there

You may be entering this node for the first time, retrying after a crash, or re-entering after the plan-review gate
bounced the plan. Do not assume. Run `blizzard runner artifact list` first.

- A `plan` asset already exists — read it (`blizzard runner artifact get plan --content`) and revise it. Do not start
  over.
- A `plan-findings` asset came in with you — it names what to fix. Address it.
- Neither exists — this is a first draft.

## What the plan must contain

- **The feature branch.** Name it at the top of the plan: `feat/<slug>`, where `<slug>` is a short kebab-case
  description of the change (for example, `feat/runner-crash-resume`). Every repo the build touches uses this one
  branch. Its name is what downstream sees — the merge messages and the delivery PR — so describe *what the change is*,
  never the environment it was built in.
- **How each change is verified.** Map every planned change to a verification method the project already declares. Where
  no declared method covers it, plan the work to build that method first.
- **Conformance to the project's architecture guidance.**
- **Every surface the change owes** — code, agent-facing context, public documentation. Each is planned work, not
  something for pre-push to catch.
- **Acceptance criteria** that reference those verification methods.

Size the plan to the work. A small change earns a small plan. Only work that needs ordered, independently verifiable
increments earns numbered **phases**.

If this workspace declares its own planning process, author through it — its conventions govern, including how it sizes
a plan to the work.

Keep drafts and notes somewhere disposable: a path outside every repository working tree *and* outside the workspace
directory the fleet spawned you in — both are git working trees, and nothing sweeps a loose file in either. A per-chunk
directory under the machine's temporary space satisfies this; use `$BLIZZARD_CHUNK_ID` to name it. If this workspace
declares a scratch location of its own, prefer that.

Submit the plan as this node's `plan` asset before you declare done: run `blizzard runner artifact create --name plan`
with the content on stdin.

Also submit a `plan-finding-refutes` asset, and make it **cumulative**: run
`blizzard runner artifact create --name plan-finding-refutes` with the content on stdin.

This asset is **replaced, not appended to** — the gate sees only your newest submission and never looks for an older
one. So restate **every refutation still standing**, including any the gate already accepted in an earlier round, each
marked `open` or `accepted`. Read your own previous submission first
(`blizzard runner artifact get plan-finding-refutes --content`) and carry it forward. Only write "nothing to refute"
when nothing is standing — on a first draft, or once every refuted finding is resolved or withdrawn.
