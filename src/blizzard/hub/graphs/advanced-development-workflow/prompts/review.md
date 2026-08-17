# Review (advanced-development-workflow)

You are working a chunk's **review** node-step with cold eyes — a fresh session that did not build this work. Review the
change as it stands against the work item's intent and the plan of record — the `reviewed-plan` asset
(`blizzard runner artifact get reviewed-plan --content`), the plan as it left the gate with its folded improvements, not
the `plan` draft it was folded from. Review observes, build repairs: do not commit fixes here.

## Start from what is actually there

Run `blizzard runner artifact list` first. The `git_commit` artifacts name the branch and commit per repo — take the
newest per repo, since a rebase or a base-merge may have moved a branch since build declared it, and confirm each repo's
worktree is actually on that commit before you read a line of the diff. Reviewing a stale checkout produces findings
against code nobody will ship.

A `review-findings` asset from an earlier round means this change has been here before. This pass is a **full cold read
of the change as it now stands**, not a delta over what changed since — re-report anything still wrong under a fresh id.

A `review-finding-refutes` asset holds findings the build declined rather than fixed, with its arguments. Read it
**before** you review, and adjudicate it (below).

## Adjudicate the refutations first

The newest `review-finding-refutes` asset is the whole record — an older submission is shadowed by design and carries no
standing, and the build is required to restate every standing refutation in its newest submission, so do not go looking
for older epochs. Every entry gets an explicit answer from you; silence is not acceptance, and an unanswered refutation
is still an open finding.

- An entry already marked **`accepted`** was adjudicated in an earlier round. It stays accepted: do not re-adjudicate
  it, do not raise that finding again, and carry it into your own `review-findings` asset as still-accepted, naming the
  anchor, so the record survives.
- **Accept** an `open` entry when the argument holds — the finding was wrong, rested on a false premise, or asked for
  work this change's scale does not warrant. Do not raise the finding again; say in `review-findings` that you accepted
  it, naming the anchor and why.
- **Reject** it when the argument does not hold: re-raise the finding and **answer the argument** — do not simply
  restate the original finding.

Match a refutation to a finding by its **anchor**, not its id: your ids restart at `F1` every submission, so the anchor
is the only stable handle across a fresh cold pass. An asset with no recognizable entries — build status from the
completion fallback rather than refutations — reads as "nothing refuted"; record that reading and move on. This step is
what keeps a cold read from re-discovering the same declined finding every round: a refutation is a claim you
adjudicate, never a veto.

## The axes

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — clarity, simplicity, fit with existing patterns.

If this workspace provides review tooling, use it — blizzard builds no review machinery of its own. Exercise the
change's end-to-end flows inside the chunk's environment, where the services are available to drive.

## Submit

Submit your findings as the node's `review-findings` asset before you declare done: run
`blizzard runner artifact create --name review-findings` with the content on stdin — what you checked per axis, what
passed, how you adjudicated every refutation, and every finding with:

- **id** — `F1`, `F2`, …, stable within this submission only.
- **severity** — `blocking` for anything that must be fixed before this passes, `should-fix` for a real defect below
  that bar. Record should-fix findings too — a non-blocking defect only reaches a disposition if it is written down.
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`; the repo prefix is what keeps an anchor unambiguous
  once a chunk spans more than one repo.
- one or two specific, actionable sentences — what is wrong, not just where.

The entry fields above are restated from the docket, the canonical definition of this format; read it in full with
`blizzard runner artifact get docket --scope graph --content`. If that command fails — any error, rather than the
docket's text — record your findings in the fields as stated above and do not retry.
