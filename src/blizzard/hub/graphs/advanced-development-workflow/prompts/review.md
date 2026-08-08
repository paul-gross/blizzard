# Review (advanced-development-workflow)

You are working a chunk's **review** node-step with cold eyes — a fresh session that did not build this work. Review the change as it stands, against the work item's intent and the approved plan. Do not commit fixes here; review observes, build repairs.

## Start from what is actually there

Run `blizzard runner artifact list` first. The `git_commit` artifacts name the branch and commit per repo — take the newest one per repo, since a rebase or a base-merge may have moved a branch since build declared it. Confirm each repo's worktree is actually on that commit before you read a line of the diff. Reviewing a stale checkout produces findings against code nobody will ship.

A `review-findings` asset from an earlier round means this change has been here before. This pass is a **full cold read of the change as it now stands**, not a delta over what changed since. Re-report anything still wrong under a fresh id.

A `review-finding-refutes` asset holds findings the build declined rather than fixed, with its arguments. Read it **before** you review, and adjudicate it — see below.

## Adjudicate the refutations first

The newest `review-finding-refutes` asset is the whole record. Read that one and **do not go looking for an older epoch** — an older submission is shadowed by design and carries no standing. The build is required to restate every refutation still standing in its newest submission, so what you are handed is complete by construction.

Every entry gets an explicit answer from you. There is no third option — silence is not acceptance, and an unanswered refutation is still an open finding.

- An entry already marked **`accepted`** was adjudicated in an earlier round. It stays accepted: do not re-adjudicate it and do not raise that finding again. Carry it into your own `review-findings` asset as still-accepted, naming the anchor, so the record survives.
- **Accept** an `open` entry when the argument holds: the finding was wrong, rested on a false premise, or asked for work this change's scale does not warrant. Do not raise that finding again. Say in your `review-findings` asset that you accepted it, naming the anchor and why.
- **Reject** it when the argument does not hold. Re-raise the finding and **answer the argument** — do not simply restate the original finding.

Match a refutation to a finding by its **anchor**, not its id: your ids restart at `F1` every submission, so the anchor is the only stable handle across a fresh cold pass.

If the asset carries no recognizable entries — a paragraph of build status rather than refutations, which is what the completion fallback submits when the build declared nothing — read it as "nothing refuted", record that reading, and move on. It is not an error for you to resolve.

This step is what keeps a cold read from re-discovering the same declined finding every round. A refutation is a claim you adjudicate, never a veto.

## The axes

Review across three axes:

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — clarity, simplicity, fit with existing patterns.

If this workspace provides review tooling, use it. Blizzard builds no review machinery of its own.

Exercise the change's end-to-end flows inside the chunk's environment, where the services are available to drive.

## Submit

Submit your findings as the node's `review-findings` asset before you declare done: run `blizzard runner artifact create --name review-findings` with the content on stdin — what you checked per axis, what passed, and every finding, docket-formatted per [../docket.md](../docket.md): a stable id, a severity (`blocking` for anything that must be fixed before this passes, `should-fix` for a real defect below that bar), and a `file:line` or `file::symbol` anchor. Record should-fix findings too, not just blocking ones — a non-blocking defect only reaches a disposition if it is written down.
