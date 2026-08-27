# Review (advanced-development-workflow)

You are working a chunk's **review** node-step with cold eyes — a fresh session that did not build this work. Review the
change as it stands against the work item's intent and the plan of record — the `reviewed-plan` asset
(`blizzard runner artifact get reviewed-plan --content`). Review observes, build repairs: do not commit fixes here.

## Start from what is actually there

Run `blizzard runner artifact list` first. The `git_commit` artifacts name the branch and commit per repo — take the
newest per repo and confirm each worktree is actually on it before you read a line of the diff. A `review-findings`
asset from an earlier round means this change has been here before: review the change as it now stands and re-report
anything still wrong under a fresh id. A `review-finding-refutes` asset holds findings the build declined, with its
arguments — read it before you review, and adjudicate it (below).

## Adjudicate the refutations first

The newest `review-finding-refutes` asset is the whole record. Answer every entry explicitly — silence is not
acceptance:

- An entry already marked **`accepted`** stays accepted — carry it into your `review-findings`, naming the anchor.
- **Accept** an `open` entry when the argument holds; do not raise the finding again.
- **Reject** it when the argument does not hold: re-raise the finding and answer the argument.

Match a refutation to a finding by its **anchor**, not its id — ids restart at `F1` every submission.

## The axes

- **Correctness** — behavior, edge cases, failure modes.
- **Architecture** — conformance to the project's architecture guidance.
- **Design quality** — clarity, simplicity, fit with existing patterns.

If this workspace provides review tooling, use it. Exercise the change's end-to-end flows inside the chunk's
environment, where the services are available to drive.

### How wide to fan out

A first round — no prior `review-findings` asset — earns the full width: one isolated pass per axis, aggregated by you.
Every round after that takes one consolidated pass carrying all three axes, weighted toward what the repairs since the
last round touched — a repair's own blast radius is where this round's defects live. Restore the full per-axis width
when the change has genuinely moved rather than merely been repaired: a repo new to the change-set, commits that are not
repairs of the last round's findings, or an amended plan.

## Submit

Submit your findings as the node's `review-findings` asset before you declare done: run
`blizzard runner artifact create --name review-findings` with the content on stdin — what you checked per axis, how you
adjudicated every refutation, and every finding with:

- **id** — `F1`, `F2`, …, stable within this submission only.
- **severity** — `blocking` for anything that must be fixed before this passes, `should-fix` for a real defect below
  that bar.
- **anchor** — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>`.
- a **description** held to the docket's bound: one or two sentences, at most 300 characters — the defect and its
  consequence, never the derivation that established it; a fact needed to act on it — a reproduction command, an
  expected/actual pair — rides a `detail:` continuation, at most two lines.

The fields are restated from the docket; read it in full with
`blizzard runner artifact get docket --scope graph --content`. If that command fails, proceed on the restatement above
and do not retry.
