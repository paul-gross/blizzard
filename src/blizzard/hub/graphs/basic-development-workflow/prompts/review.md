# Review

Review cold against the work item. Earlier `review-findings` make this a delta re-visit; otherwise review the whole
change.

## Check out the change

Run `blizzard runner artifact list` first. Take the newest `git_commit` per repo; check out and confirm each tip in its
leased environment before reading the diff.

## Review the work

Judge each axis — correctness, architecture (conformance to the project's architecture guidance), and design quality
(conformance to the project's code-quality standards) — against the project's review criteria: before judging, open them
and, per axis, the documents they route this change's code to, until you reach the rules themselves. Judge from what you
read, not memory, naming the criterion a finding violates when one applies.

Use workspace review tooling; exercise end-to-end flows in the chunk's environment. Build repairs; commit no fix.

Anchor findings at `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>` so they can be acted on and refuted.

Grade each finding `blocking` — must be fixed before landing — or `should-fix` — a real defect below that bar. A
should-fix also carries `in-diff` when its anchor is a line or passage this change added or modified, else
`outside-diff` (untouched code or an exposed defect). The tag only locates it; the round decides if it blocks.

## A re-visit reviews the delta

On a re-visit, read the newest findings with `blizzard runner artifact get review-findings --content` and take one pass
over the delta since the tip they recorded per repo. Where no tip was recorded or its commit cannot be fetched, the
delta is the whole change. Confirm every prior finding fixed or refuted and accepted, re-reporting any that is not, and
judge the delta sound, a repair's blast radius included; exercise only the end-to-end flows the delta reaches.

A rebase makes a tip-to-tip diff mostly base-branch motion. Scope by the change's commits with `git range-diff`; return
to a full review only for a new repo or commits reshaping the change rather than repairing findings.

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

Submit `review-finding-delta` via `blizzard runner artifact create --name review-finding-delta`: JSON
`{"entries": [...]}`. Dispositions: `fixed`, `refuted`, `deferred`. Example:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`. Empty: `{"entries": []}`. Shape:
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit findings

Submit before declaring done: `blizzard runner artifact create --name review-findings`, content on stdin — the tip you
judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (whole change, or the
delta and what it resolved), what you checked per axis and the path of each criteria document read for it, what passed,
every finding with its severity and, on a should-fix, its tag. On `fail` the asset rides back to build, so make each
finding specific and actionable.
