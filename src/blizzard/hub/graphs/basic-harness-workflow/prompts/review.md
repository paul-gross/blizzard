# Review

You work this prompt at a chunk's `review` node-step, in a fresh session that did not build the work, reading the change
cold against the work item's intent. Review observes and iterate repairs: no fix is committed from this node.

## Open the pass

Open with `blizzard runner artifact list`. Its `git_commit` artifacts name each repo's branch and commit; take the
newest entry per repo, since a rebase may have moved a branch, then check each repo out to that commit and confirm it
before reading any diff.

A `review-findings` asset from an earlier round makes this pass a re-visit, scoped to the delta (below); with none, it
is a first round and reads the whole change.

## Judge the change

Judge the change across correctness, architecture, and design quality, weighed for prose and convention rather than only
code, since this lane's subject is harness work — agent-facing conventions, skills, prompts, and docs.

- **Correctness** — does the text say what it means? Follow each instruction literally; one that only works when read
  charitably is a defect.
- **Architecture** — does the material belong where it was put, and does the routing to it hold?
- **Design quality** — is the change the shortest thing that changes behavior? Prose restating what an agent already
  does is cost without effect.

Use the review tooling this workspace provides where it exists.

## A re-visit reviews the delta

On a re-visit, read the newest findings with `blizzard runner artifact get review-findings --content` and take one pass
over the delta since the tip they recorded per repo, leaving the unchanged remainder alone. Where no tip was recorded,
or the recorded commit cannot be fetched, the delta is the whole change. Confirm every prior finding is resolved —
fixed, or refuted and accepted — re-reporting any that is not, and judge the delta sound on every axis.

A reworded sentence can change how the untouched text around it reads: read each changed passage within its whole
section, and follow anything it renames or re-routes to wherever that is used. That reach is part of the delta; the rest
of the change is not.

A rebase since the recorded tip makes a tip-to-tip diff mostly the base branch's motion, so scope by the change's own
commits instead, with `git range-diff` across the rebase. Return to a full read only when the change moved rather than
was repaired: a repo new to the change-set, or commits that reshape the change rather than answer the last round's
findings.

## Adjudicate iterate's refutations

The `review-finding-refutes` asset holds findings a prior `iterate` visit declined rather than fixed, with its
arguments. Absent on a first pass — this review always precedes any `iterate` visit — which is normal, not a gap to
flag. Where present, its newest submission restates every refutation still standing and is therefore the whole record,
so don't go looking for an older, deliberately shadowed epoch.

Match a refutation to its finding by its anchor rather than its id: every pass renumbers, and the anchor is the only
stable handle. A refutation is a claim to adjudicate, never a veto — findings about prose and convention are judgements
rather than failed assertions, so good-faith disagreement is ordinary, and neither reflexive deference nor reflexive
re-raising is review.

Answer every entry in that asset explicitly, since silence is not acceptance. One already marked `accepted` stays
accepted and is carried into this round's findings as still-accepted with its anchor. An `open` entry is accepted when
its argument holds — the finding was wrong, rested on a false premise, or demanded work beyond this change's scale — and
is then recorded with its anchor and reason and never raised again; it is rejected when the argument does not hold, the
finding re-raised and the argument itself answered. A finding whose refutation is accepted is resolved exactly as if it
had been fixed and does not block `pass`.

## Submit the findings

Every finding is specific, actionable, and anchored at a file and line, since on a `fail` the asset rides back into the
iterate node's envelope for the next attempt. Before declaring done you MUST run
`blizzard runner artifact create --name review-findings` with the findings on stdin, recording the tip judged per repo
(`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (the whole change, or the delta and the
prior findings it resolved), what was checked, what passed, and every blocking issue.
