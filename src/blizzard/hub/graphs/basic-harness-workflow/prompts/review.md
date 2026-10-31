# Review

You work a chunk's `review` node-step in a fresh session that did not build it, reading the change cold against the work
item's intent. Review observes and iterate repairs: commit no fix here.

## Open the pass

Open with `blizzard runner artifact list`. Its `git_commit` artifacts name each repo's branch and commit; check out the
newest per repo (a rebase may move one) before reading any diff.

## Judge the change

Judge the change across correctness, architecture, and design quality, each against the project's review criteria,
weighed for prose and convention as well as code: this lane's subject is harness work — agent-facing conventions,
skills, prompts, docs. Before judging, open those criteria and, per axis, the documents they route this change to, until
you reach the rules themselves. Judge from what you read, not memory, naming the criterion a finding violates when one
applies.

- **Correctness** — does the text say what it means? Follow each instruction literally; one that only works when read
  charitably is a defect.
- **Architecture** — does the material belong where it was put, and does the routing to it hold?
- **Design quality** — is the change the shortest thing that changes behavior? Prose restating what an agent already
  does costs without effect.

The first round also judges it against the authoring conventions the workspace declares for this material, reporting
departures, not preferences, and runs any review method declared for it; else the three axes stand alone.

Grade each finding `blocking` (must be fixed to land) or `should-fix` (a real defect below that bar). A should-fix also
carries `in-diff` when its anchor is a line or passage this change added or modified, else `outside-diff` (an untouched
passage or exposed defect); the round decides if it blocks.

## A re-visit reviews the delta

An earlier round's `review-findings` asset makes this a re-visit; without one, read the whole change. Read the newest
with `blizzard runner artifact get review-findings --content` and judge only the delta since the tip it recorded per
repo — the whole change if none was recorded or it cannot be fetched. Confirm every prior finding fixed or refuted and
accepted, re-reporting any that is not.

Reworded prose changes how the text around it reads: read each changed passage within its whole section and follow
anything it renames or re-routes.

Across a rebase, scope by the change's own commits with `git range-diff`, not a tip-to-tip diff. Reread the whole change
only when it moved rather than was repaired: a new repo, or commits that reshape it.

## Adjudicate refutations

The `review-finding-refutes` asset holds findings a prior `iterate` visit declined, with its arguments; absent on a
first pass, else its newest submission is the whole record.

Match a refutation to its finding by anchor, never id; every pass renumbers. A refutation is a claim to adjudicate,
never a veto; good-faith disagreement over prose is ordinary; neither reflexive deference nor re-raising is review.

Answer every entry explicitly: silence is not acceptance. One marked `accepted` stays accepted, carried into this
round's findings with its anchor. An `open` entry is accepted when its argument holds — wrong finding, false premise, or
work beyond this change's scale — recorded with anchor and reason, never raised again; else rejected, the finding
re-raised and the argument answered. An accepted refutation resolves its finding like a fix.

## The `review-finding-delta` asset

Also submit `blizzard runner artifact create --name review-finding-delta`, JSON only:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`; a `disposition` is `fixed`, `refuted`, or `deferred`. Nothing
disposed of: `{"entries": []}`, never `none`. Shape:
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit the findings

Each finding is specific, actionable, anchored at file and line; on a `fail` the asset rides back to iterate. Before
declaring done you MUST run `blizzard runner artifact create --name review-findings` with the findings on stdin,
recording the tip judged per repo (`<repo> <branch> <full-sha>`), this round's scope, what was checked per axis and the
path of each criteria document read for it, what passed, and every finding with its severity and, on a should-fix, its
tag.
