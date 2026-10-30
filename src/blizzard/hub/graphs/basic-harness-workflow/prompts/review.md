# Review

You work a chunk's `review` node-step in a fresh session that did not build it, reading the change cold against the work
item's intent. Review observes and iterate repairs: commit no fix here.

## Open the pass

Open with `blizzard runner artifact list`. Its `git_commit` artifacts name each repo's branch and commit; take the
newest per repo (a rebase may have moved one) and check it out before reading any diff.

## Judge the change

Judge the change across correctness, architecture, and design quality, each against the project's review criteria,
weighed for prose and convention as well as code: this lane's subject is harness work — agent-facing conventions,
skills, prompts, and docs. Name the criterion a finding violates when one applies.

- **Correctness** — does the text say what it means? Follow each instruction literally; one that only works when read
  charitably is a defect.
- **Architecture** — does the material belong where it was put, and does the routing to it hold?
- **Design quality** — is the change the shortest thing that changes behavior? Prose restating what an agent already
  does is cost without effect.

The first round also judges the change against the authoring conventions the workspace declares for this kind of
material, found by following its own routing. Report departures from those conventions, not your preferences. Where the
workspace declares a review method for that material, run it; else the three axes above stand alone.

Grade each finding `blocking` (must be fixed before this can land) or `should-fix` (a real defect below that bar). A
should-fix also carries `in-diff` when its anchor is a line or passage this change's commits added or modified, else
`outside-diff` (an untouched passage, or a defect the change exposed); the round decides if it blocks.

## A re-visit reviews the delta

An earlier round's `review-findings` asset makes this pass a re-visit; without one, read the whole change. Read the
newest with `blizzard runner artifact get review-findings --content` and judge only the delta since the tip it recorded
per repo — the whole change where none was recorded or it cannot be fetched. Confirm every prior finding is fixed or
refuted and accepted, re-reporting any that is not.

Reworded prose changes how untouched text around it reads: read each changed passage within its whole section and follow
anything it renames or re-routes.

Across a rebase, scope by the change's own commits with `git range-diff`, not a tip-to-tip diff. Read the whole change
again only when it moved rather than was repaired: a new repo, or commits that reshape it.

## Adjudicate iterate's refutations

The `review-finding-refutes` asset holds findings a prior `iterate` visit declined, with its arguments; absent on a
first pass, else its newest submission is the whole record.

Match a refutation to its finding by anchor, never id: every pass renumbers. A refutation is a claim to adjudicate,
never a veto; good-faith disagreement over prose is ordinary; neither reflexive deference nor re-raising is review.

Answer every entry explicitly: silence is not acceptance. One marked `accepted` stays accepted, carried into this
round's findings with its anchor. An `open` entry is accepted when its argument holds — wrong finding, false premise, or
work beyond this change's scale — recorded with anchor and reason, never raised again; rejected when it does not, the
finding re-raised and the argument answered. An accepted refutation resolves its finding as a fix would.

## The `review-finding-delta` asset

Also submit this asset, as `blizzard runner artifact create --name review-finding-delta`: JSON, never prose —
`{"entries": [...]}`, one entry per finding this round disposed of, each with a `ref` (the finding's id) and a
`disposition` of `fixed`, `refuted`, or `deferred` (a still-open should-fix a passing review leaves unanswered; it also
carries `severity`, `scope`, `class`, `locus`, and `summary`). Example:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`. With nothing disposed of, submit `{"entries": []}` — not `none`.
`artifact create` refuses a body that does not parse, naming the field; read the full shape with
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit the findings

Every finding is specific, actionable, and anchored at a file and line; on a `fail` the asset rides back to iterate.
Before declaring done you MUST run `blizzard runner artifact create --name review-findings` with the findings on stdin,
recording the tip judged per repo (`<repo> <branch> <full-sha>`), this round's scope, what was checked, what passed, and
every finding with its severity and, on a should-fix, its tag.
