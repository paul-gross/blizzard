# Review (advanced-development-workflow)

Review cold against the work item and `reviewed-plan` asset (`blizzard runner artifact get reviewed-plan --content`).
Review observes; build repairs. Commit no fix here.

## Start from what is there

Run `blizzard runner artifact list` first. Take the newest `git_commit` per repo and confirm each worktree is on it
before reading the diff. Earlier `review-findings` make this a re-visit; adjudicate `review-finding-refutes` first.

## Adjudicate refutations first

The newest `review-finding-refutes` is the whole record. Answer every entry by **anchor**, not id (ids restart at `F1`):
carry `accepted` entries into `review-findings`; accept a sound `open` argument and do not re-raise it; reject an
unsound one, re-raising the finding and answering the argument.

## The axes

Before judging, open the project's review criteria and, per axis, the documents they route this change's code to, until
you reach the rules themselves. Judge each axis from what you read, not memory, naming the criterion a finding violates
when one applies.

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — conformance to the project's code-quality standards: clarity, simplicity, fit with patterns.

Use workspace review tooling and exercise end-to-end flows in the chunk's environment.

### A re-visit reviews the delta

A first round — no prior `review-findings` asset — reviews the whole change at full width: one isolated pass per axis,
aggregated by you. A later round takes one pass over all three axes on the delta since the tip the newest
`review-findings` recorded per repo (none recorded: the whole change): confirm every prior finding fixed or refuted and
accepted, re-reporting any that is not under a fresh id, and judge the delta sound on every axis, a repair's blast
radius included.

After a rebase or base merge, scope by the change's own commits, not a tip-to-tip diff — `git range-diff` across a
rebase, `git show --remerge-diff` on a merge, the `pre-push-summary` or `resolve-report` naming what integration
touched. Restore full width only when the change moved rather than was repaired: a new repo, commits beyond repairs of
the last round's findings, or an amended plan.

## The `review-finding-delta` asset

Submit `review-finding-delta` via `blizzard runner artifact create --name review-finding-delta`: JSON
`{"entries": [...]}`. Dispositions: `fixed`, `refuted`, `deferred`. Example:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`. Empty: `{"entries": []}`. Shape:
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit

Before declaring done, run `blizzard runner artifact create --name review-findings`, content on stdin — the tip you
judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (whole change, or the
delta and what it resolved), what you checked per axis and the path of each criteria document read for it, how you
adjudicated each refutation, and every finding with:

- **id** — `F1`, `F2`, …, stable within a submission.
- **severity** — `blocking` for what must be fixed before landing, `should-fix` for a real defect below that bar; a
  `should-fix` also carries `in-diff` when its anchor is a line or passage this change added or modified, else
  `outside-diff` (untouched code, an exposed defect, a chunk asset) — the round decides if it blocks.
- **anchor** — `<repo>/<path>:<line>`, `<repo>/<path>::<symbol>`, or `<asset-name>::<section>`.
- a **description** — at most 300 characters: the defect and its consequence, never the derivation; a fact needed to act
  on it (a repro, expected/actual) rides a `detail:` continuation of at most two lines.

The fields restate the docket; read it in full with `blizzard runner artifact get docket --scope graph --content`, or
proceed on them if it is unreadable or empty.
