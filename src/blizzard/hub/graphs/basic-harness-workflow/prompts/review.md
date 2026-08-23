# Review

You work this prompt at a chunk's `review` node-step, in a fresh session that did not build the work, reading the change
cold against the work item's intent. Review observes and build repairs: no fix is committed from this node.

## Open the pass

Open with `blizzard runner artifact list`. Its `git_commit` artifacts name each repo's branch and commit; take the
newest entry per repo, since a rebase may have moved a branch, then check each repo out to that commit and confirm it
before reading any diff.

A `review-findings` asset from an earlier round means the change has been here before. The pass is still a full cold
read of the change as it now stands rather than a delta, re-reporting anything still wrong.

## Judge the change

Judge the change across correctness, architecture, and design quality, weighed for prose and convention rather than only
code, since this lane's subject is harness work — agent-facing conventions, skills, prompts, and docs.

- **Correctness** — does the text say what it means? Follow each instruction literally; one that only works when read
  charitably is a defect.
- **Architecture** — does the material belong where it was put, and does the routing to it hold?
- **Design quality** — is the change the shortest thing that changes behavior? Prose restating what an agent already
  does is cost without effect.

Use the review tooling this workspace provides where it exists.

## Adjudicate the build's refutations

The `review-finding-refutes` asset holds findings the build declined rather than fixed, with its arguments. Its newest
submission restates every refutation still standing and is therefore the whole record, so don't go looking for an older,
deliberately shadowed epoch.

Match a refutation to its finding by its anchor rather than its id: a fresh cold pass renumbers, and the anchor is the
only stable handle. A refutation is a claim to adjudicate, never a veto — findings about prose and convention are
judgements rather than failed assertions, so good-faith disagreement is ordinary, and neither reflexive deference nor
reflexive re-raising is review.

Answer every entry in that asset explicitly, since silence is not acceptance. One already marked `accepted` stays
accepted and is carried into this round's findings as still-accepted with its anchor. An `open` entry is accepted when
its argument holds — the finding was wrong, rested on a false premise, or demanded work beyond this change's scale — and
is then recorded with its anchor and reason and never raised again; it is rejected when the argument does not hold, the
finding re-raised and the argument itself answered. A finding whose refutation is accepted is resolved exactly as if it
had been fixed and does not block `pass`.

## Submit the findings

Every finding is specific, actionable, and anchored at a file and line, since on a `fail` the asset rides back into the
build node's envelope for the next build attempt. Before declaring done you MUST run
`blizzard runner artifact create --name review-findings` with the findings on stdin, recording what was checked, what
passed, and every blocking issue.
