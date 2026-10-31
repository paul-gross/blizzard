# Review

This is a chunk's review node-step, run cold by a fresh session that did not build the work: review the change against
the work item's intent. An earlier `review-findings` asset makes this pass a re-visit, scoped to the delta (below); with
none, it is a first round over the whole change.

## Check out the change

Run `blizzard runner artifact list` first; its `git_commit` artifacts name each repo's branch and commit — use the
newest per repo (a rebase may have moved it). Check each repo out to that commit in the leased environment(s) and
confirm it before reading any diff.

## Review the work

Judge each axis — correctness, architecture (conformance to the project's architecture guidance), and design quality —
against the project's review criteria, naming the criterion a finding violates when one applies.

Use the review tooling this workspace provides. Exercise the change's end-to-end flows in the chunk's environment.
Commit no fix from this node — review observes, build repairs.

Anchor every finding — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>` — an unanchored finding can be neither acted
on nor matched to a refutation.

Grade each finding `blocking` — must be fixed before this can land — or `should-fix` — a real defect below that bar. A
should-fix also carries `in-diff` when its anchor is a line or passage this change's commits added or modified, else
`outside-diff` (untouched neighbouring code, or a defect the change exposed). The tag only locates it; the round decides
whether it blocks.

## A re-visit reviews the delta

On a re-visit, read the newest findings with `blizzard runner artifact get review-findings --content` and take one pass
over the delta since the tip they recorded per repo. Where no tip was recorded or its commit cannot be fetched, the
delta is the whole change. Confirm every prior finding is resolved — fixed, or refuted and accepted — re-reporting any
that is not, and judge the delta sound, a repair's blast radius included; exercise only the end-to-end flows the delta
reaches.

A rebase since the recorded tip makes a tip-to-tip diff mostly base-branch motion, so scope by the change's own commits
instead, with `git range-diff` across the rebase. Return to a full review only when the change moved rather than was
repaired: a new repo in the change-set, or commits that reshape the change rather than answer the last round's findings.

## Adjudicate refutations

The `review-finding-refutes` asset holds findings the build declined, with its arguments; the newest epoch is the
complete record — never dig for an older, shadowed one. One with no recognizable entries (e.g. a bare build status)
reads as "nothing refuted"; move on.

Match a refutation to a finding by its anchor, never its id — every pass renumbers. Answer every refutation entry
explicitly — silence is not acceptance. A refutation is a claim you adjudicate, never a veto; an accepted one resolves
its finding like a fix and does not block `pass`.

- Accept an `open` entry whose argument holds — finding wrong, false premise, or work beyond this change's scale; never
  raise it again, and record the acceptance with anchor and why.
- Reject an entry whose argument fails: re-raise the finding and answer the argument rather than restating it.
- An entry marked `accepted` stays accepted: neither re-adjudicate nor re-raise it; carry it into your findings as
  still-accepted with its anchor so the record survives.

## Submit findings

Submit findings before declaring done: `blizzard runner artifact create --name review-findings`, content on stdin — the
tip you judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (the whole
change, or the delta and what it resolved), what you checked, what passed, every finding with its severity and, on a
should-fix, its tag. On `fail` the findings asset rides back into build's envelope, so make each finding specific and
actionable.
