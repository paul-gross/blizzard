# Review

Review cold against the work item. Review observes; iterate repairs. Commit no fix here.

## Open the pass

Run `blizzard runner artifact list`. Take the newest `git_commit` per repo; check it out before reading any diff.

## Judge the change

Judge correctness, architecture, and design quality against project review criteria, weighing prose and convention as
well as code. Name the violated criterion when one applies.

- **Correctness** — does the text say what it means? Follow each instruction literally; one that only works when read
  charitably is a defect.
- **Architecture** — does the material belong where it was put, and does the routing to it hold?
- **Design quality** — is the change the shortest thing that changes behavior? Prose restating what an agent already
  does is cost without effect.

On the first round, follow workspace routing to its authoring conventions and review method. Report departures from
conventions, not preferences; run that method when declared.

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

Submit `review-finding-delta` via `blizzard runner artifact create --name review-finding-delta`: JSON
`{"entries": [...]}`. Dispositions: `fixed`, `refuted`, `deferred`. Example:
`{"entries":[{"ref":"F1","disposition":"fixed"}]}`. Empty: `{"entries": []}`. Shape:
`blizzard runner artifact get --scope system review/finding-format --content`.

## Submit the findings

Every finding is specific, actionable, and anchored at a file and line; on a `fail` the asset rides back to iterate.
Before declaring done you MUST run `blizzard runner artifact create --name review-findings` with the findings on stdin,
recording the tip judged per repo (`<repo> <branch> <full-sha>`), this round's scope, what was checked, what passed, and
every finding with its severity and, on a should-fix, its tag.
