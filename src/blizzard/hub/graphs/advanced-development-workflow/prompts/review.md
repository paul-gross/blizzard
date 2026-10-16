# Review (advanced-development-workflow)

You work a chunk's **review** node-step cold — a fresh session that did not build this work. Review the change against
the work item's intent and the plan of record — the `reviewed-plan` asset
(`blizzard runner artifact get reviewed-plan --content`). Review observes, build repairs: do not commit fixes here.

## Start from what is there

Run `blizzard runner artifact list` first. The `git_commit` artifacts name the branch and commit per repo — take the
newest per repo and confirm each worktree is on it before you read the diff. A `review-findings` asset from an earlier
round makes this a re-visit (below). A `review-finding-refutes` asset holds findings the build declined, with its
arguments — adjudicate it first (below).

## Adjudicate refutations first

The newest `review-finding-refutes` asset is the whole record — answer every entry explicitly, matched by **anchor**,
never id (ids restart at `F1` every submission): an **`accepted`** entry stays accepted, carried into your
`review-findings` with its anchor; **accept** an `open` entry whose argument holds and do not raise the finding again;
**reject** one whose argument does not hold — re-raise the finding and answer the argument.

## The axes

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — clarity, simplicity, fit with existing patterns.

Use the workspace's review tooling where one exists, and exercise the change's end-to-end flows in the chunk's
environment.

### A re-visit reviews the delta

A first round — no prior `review-findings` asset — reviews the whole change at full width: one isolated pass per axis,
aggregated by you. A later round takes one consolidated pass carrying all three axes over the delta since the tip the
newest `review-findings` recorded per repo (none recorded: the whole change): confirm every prior finding is resolved —
fixed, or refuted and accepted — re-reporting any that is not under a fresh id, and judge the delta sound on every axis,
a repair's blast radius included.

After a rebase or a base merge a tip-to-tip diff is mostly the base branch's motion: scope by the change's own commits —
`git range-diff` across a rebase, `git show --remerge-diff` on a merge, the `pre-push-summary` or `resolve-report`
naming what integration touched. Restore full per-axis width only when the change moved rather than was repaired: a
repo new to the change-set, commits that are not repairs of the last round's findings, or an amended plan.

## Submit

Before you declare done, run `blizzard runner artifact create --name review-findings`, content on stdin — the tip you
judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (whole change, or the
delta and what it resolved), what you checked per axis, how you adjudicated every refutation, and every finding with:

- **id** — `F1`, `F2`, …, stable within this submission only.
- **severity** — `blocking` for what must be fixed before this passes, `should-fix` for a real defect below that bar; a
  `should-fix` also carries `in-diff` when its anchor is a line or passage this change's own commits added or modified,
  else `outside-diff` (untouched neighbouring code, a defect the change merely exposed, a chunk asset) — the judgement
  decides by round whether it blocks.
- **anchor** — `<repo>/<path>:<line>`, `<repo>/<path>::<symbol>`, or `<asset-name>::<section>`.
- a **description** — one or two sentences, at most 300 characters: the defect and its consequence, never the
  derivation; a fact needed to act on it (a reproduction command, an expected/actual pair) rides a `detail:`
  continuation, at most two lines.

The fields restate the docket; read it in full with
`blizzard runner artifact get docket --scope graph --content`. If that read fails or comes back empty, proceed on the
restatement above.
