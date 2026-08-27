# Build (advanced-development-workflow)

You are working a chunk's **build** node-step. The approved plan is in the envelope as the `reviewed-plan` asset — the
plan as it left the plan-review gate, with the gate's improvements already folded in. Read it with
`blizzard runner artifact get reviewed-plan --content` and implement it. Where it is phased, implement the phases **in
order**: a phase starts only after the previous one is complete, and each phase's change stays scoped to that phase. You
don't reorder them. Parallelize them with subagents to manage context as needed. Where it is not phased, implement it as
written.

## Start from what is actually there

You may be arriving from anywhere — a first entry, a retry after a crash, or a bounce back from verify, review, resolve,
or delivery. Before you change anything, look:

- In each repo you expect to touch: which branch is checked out, whether the working tree is clean, and what the branch
  already carries beyond the base branch.
- `blizzard runner artifact list` — what is already declared for this chunk, and what assets arrived with you.

Continue from what you find.

## What must be true when you finish

1. **One branch, the one the plan names.** Your work exists as commits on `feat/<slug>` in every repo you changed.

2. **The branch is pushed** to each repo's origin.

3. **Every repo you touched is declared.** For each one, run
   `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `--env <id>` is added if the chunk
   holds more than one environment.

4. **The refutation channel is submitted, and it is cumulative.** Run
   `blizzard runner artifact create --name review-finding-refutes` with the content on stdin. Your newest submission is
   the entire record, so restate every refutation still standing — not just this round's. The full docket this restates
   is retrievable directly: `blizzard runner artifact get docket --scope graph --content`. If that read fails or comes
   back empty, proceed on the restatement above.

A green build or type-check is not the bar — the verify node closes your work against runtime behavior next.
