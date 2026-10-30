# Review

This is a chunk's review node-step, run cold by a fresh session that did not build the work: review the change against
the work item's intent. An earlier `review-findings` asset makes this a re-visit, scoped to the delta (below); with
none, review the whole change.

## Check out the change

Run `blizzard runner artifact list` first; its `git_commit` artifacts name each repo's branch and commit — check each
repo out to the newest (a rebase may have moved it) in the leased environment(s) and confirm it before reading any diff.

## Review the work

Judge each axis — correctness, architecture (conformance to the project's architecture guidance), and design quality
(conformance to the project's code-quality standards) — against the project's review criteria: before judging, open them
and, per axis, the documents they route this change's code to, until you reach the rules themselves. Judge from what you
read, not memory, naming the criterion a finding violates when one applies.

Use the workspace's review tooling; exercise the change's end-to-end flows in the chunk's environment. Commit no fix
here — review observes, build repairs.

Anchor every finding — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>` — an unanchored finding can be neither acted
on nor matched to a refutation.

Grade each finding `blocking` — must be fixed before landing — or `should-fix` — a real defect below that bar. A
should-fix also carries `in-diff` when its anchor is a line or passage this change added or modified, else
`outside-diff` (untouched code or an exposed defect). The tag only locates it; the round decides if it blocks.

## A re-visit reviews the delta

On a re-visit, read the newest findings with `blizzard runner artifact get review-findings --content` and take one pass
over the delta since the tip they recorded per repo. Where no tip was recorded or its commit cannot be fetched, the
delta is the whole change. Confirm every prior finding fixed or refuted and accepted, re-reporting any that is not, and
judge the delta sound, a repair's blast radius included; exercise only the end-to-end flows the delta reaches.

Across a rebase since the recorded tip, scope by the change's own commits with `git range-diff`, not a tip-to-tip diff.
Return to a full review only when the change moved rather than was repaired: a new repo, or commits that reshape the
change rather than answer the last round's findings.

## Adjudicate refutations

The `review-finding-refutes` asset holds findings the build declined, with its arguments; the newest epoch is the
complete record; never dig for an older one. One with no recognizable entries (e.g. a bare build status) reads as
"nothing refuted"; move on.

Match a refutation to a finding by its anchor, never its id — every pass renumbers. Answer every refutation entry
explicitly — silence is not acceptance. A refutation is a claim you adjudicate, never a veto; an accepted one resolves
its finding like a fix and does not block `pass`.

- Accept an `open` entry whose argument holds — finding wrong, false premise, or work beyond this change's scale; never
  raise it again, recording the acceptance with anchor and why.
- Reject an entry whose argument fails: re-raise the finding and answer the argument rather than restating it.
- An entry marked `accepted` stays accepted: neither re-adjudicate nor re-raise it; carry it into your findings as
  still-accepted with its anchor.

## The `review-finding-delta` asset

Also submit this asset, as `blizzard runner artifact create --name review-finding-delta`: JSON, never prose —
`{"entries": [...]}`, one entry per finding this round disposed of, each with a `ref` (the finding's id) and a
`disposition` of `fixed`, `refuted`, or `deferred` (a still-open should-fix a passing review leaves unanswered; it also
carries `severity`, `scope`, `class`, `locus`, and `summary`). Example:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`. With nothing disposed of, submit `{"entries": []}` — not `none`.
`artifact create` refuses a body that does not parse, naming the field; read the full shape with
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit findings

Submit before declaring done: `blizzard runner artifact create --name review-findings`, content on stdin — the tip you
judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (whole change, or the
delta and what it resolved), what you checked per axis and the path of each criteria document read for it, what passed,
every finding with its severity and, on a should-fix, its tag. On `fail` the asset rides back to build, so make each
finding specific and actionable.
