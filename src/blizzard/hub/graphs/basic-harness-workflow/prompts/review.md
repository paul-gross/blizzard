# Review

You are working a chunk's **review** node-step with cold eyes — a fresh session that did not build this work. Review the
change against the work item's intent. Do not commit fixes here; review observes, build repairs.

## Start from what is actually there

Run `blizzard runner artifact list` first. The `git_commit` artifacts name the branch and commit per repo — take the
newest one per repo, since a rebase may have moved a branch since build declared it. Check each repo out to that commit
in the leased environment(s) and confirm it before you read a line of the diff. Reviewing a stale checkout produces
findings against work nobody will ship.

A `review-findings` asset from an earlier round means this change has been here before. This pass is a full cold read of
the change **as it now stands**, not a delta over what changed since. Re-report anything still wrong.

A `review-finding-refutes` asset holds findings the build declined rather than fixed, with its arguments. The newest one
is the whole record — read that and **do not go looking for an older epoch**, which is shadowed by design. The build
restates every refutation still standing in its newest submission, so what you are handed is complete.

Give every entry an explicit answer — silence is not acceptance:

- An entry already marked **`accepted`** stays accepted. Do not re-adjudicate it or raise that finding again; carry it
  into your own findings as still-accepted, naming the anchor, so the record survives.
- **Accept** an `open` entry when the argument holds: the finding was wrong, rested on a false premise, or asked for
  work this change's scale does not warrant. Do not raise it again. Record in your findings that you accepted it, naming
  the anchor and why.
- **Reject** it when the argument does not hold. Re-raise the finding and **answer the argument**, rather than restating
  the original finding.

Match a refutation to a finding by its **anchor**, not its id — a fresh cold pass renumbers, so the anchor is the only
stable handle. A finding whose refutation you accept is resolved, exactly as if it had been fixed, and does not block
`pass`. A refutation is a claim you adjudicate, never a veto. Hold that line firmly here: findings against prose and
convention are judgements rather than failed assertions, so a good-faith disagreement is ordinary — and neither
reflexive deference nor reflexive re-raising is review.

If the asset carries no recognizable entries — build status rather than refutations, which is what the completion
fallback submits when the build declared nothing — read it as "nothing refuted" and move on.

## Review

Judge the change across correctness, architecture, and design quality. If this workspace provides review tooling, use it
— blizzard builds no review machinery of its own.

This lane is for **harness work** — agent-facing conventions, skills, prompts, and docs — so weigh those axes as they
apply to prose and convention, not only to code:

- **Correctness** — does the text say what it means? Follow each instruction literally and see where that lands. An
  instruction that only works when read charitably is a defect.
- **Architecture** — does it belong where it was put, and does the routing to it hold? A rule in the wrong file is found
  by nobody.
- **Design quality** — is it the shortest thing that changes behavior? Prose that restates what an agent already does is
  cost without effect.

## Submit

Submit your findings as the node's `review-findings` asset before you declare done: run
`blizzard runner artifact create --name review-findings` with the content on stdin — what you checked, what passed, and
every blocking issue. For a short verdict, pipe it directly:
`echo "..." | blizzard runner artifact create --name review-findings`. For a longer writeup, use a heredoc so the full
text reaches stdin intact.

On a `fail`, that asset is carried back into the build node's envelope, so make each finding specific and actionable.
Anchor every finding at a file and line; a finding about prose that names no location cannot be acted on or refuted.
