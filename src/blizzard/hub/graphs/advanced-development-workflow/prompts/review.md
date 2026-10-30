# Review (advanced-development-workflow)

You work a chunk's **review** node-step cold, in a fresh session that did not build it. Review the change against the
work item's intent and the plan of record — the `reviewed-plan` asset
(`blizzard runner artifact get reviewed-plan --content`). Review observes, build repairs: commit no fix here.

## Start from what is there

Run `blizzard runner artifact list` first. Its `git_commit` artifacts name each repo's branch and commit — confirm each
worktree is on the newest before reading any diff. An earlier round's `review-findings` asset makes this a re-visit. A
`review-finding-refutes` asset — findings the build declined — is adjudicated first.

## Adjudicate refutations first

The newest `review-finding-refutes` asset is the whole record — answer every entry explicitly, matched by **anchor**,
never id (ids restart at `F1` every submission): an **`accepted`** entry stays accepted, carried into your
`review-findings` with its anchor; **accept** an `open` entry whose argument holds and never raise it again; **reject**
one whose argument fails — re-raise the finding and answer the argument.

## The axes

Before judging, open the project's review criteria and, per axis, the documents they route this change's code to, until
you reach the rules themselves. Judge each axis from what you read, not memory, naming the criterion a finding violates
when one applies.

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — conformance to the project's code-quality standards: clarity, simplicity, fit with patterns.

Use the workspace's review tooling where it has one; exercise the change's end-to-end flows in the chunk's environment.

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

Alongside `review-findings`, submit `review-finding-delta` with
`blizzard runner artifact create --name
review-finding-delta`: JSON, never prose — `{"entries": [...]}`, one entry per
finding this round disposed of, each with a `ref` (the finding's id) and a `disposition` of `fixed`, `refuted`, or
`deferred` (a still-open should-fix a passing review leaves unanswered; it also carries `severity`, `scope`, `class`,
`locus`, and `summary`). Example: `{"entries":[{"ref":"F1","disposition":"fixed"}]}`. With nothing disposed of, submit
`{"entries": []}` — not `none`. `artifact create` refuses a body that does not parse, naming the field; read the full
shape with `blizzard runner artifact get --scope system review/finding-format --content`.

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
