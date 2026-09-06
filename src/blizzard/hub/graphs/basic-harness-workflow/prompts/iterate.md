# Iterate

You work this prompt at a chunk's `iterate` node-step: the review-fail loop's own node in this lane. It always arrives
carrying the specific findings or failure output that sent it here — the edge that reached this node names them, since
this prompt states no case of its own. Its lineage never resumed `build`'s session: it reads the code cold, with no
memory of build's own reasoning or its arguments with the reviewer.

Declare done only once every condition the rest of this prompt states holds.

## What this visit answers

Most visits answer `review`'s findings — fix or refute each, per the arrival addendum. A visit from `pre-push`'s
`significant` edge carries no findings at all: it exists because a rebase needed semantic choices or reshaped the
change, and with no `verify` node in this lane, this is the only station left to revalidate the result. On that path,
read the rebased code back the same way `build` and `review` do — does the rule say what it means, does the routing
to it land, does the instruction survive being followed literally — concentrated on whatever the rebase disturbed,
before declaring done.

## Orient before changing anything

In each repo you expect to touch, check which branch is checked out, whether the working tree is clean, and what it
carries beyond the base branch, and run `blizzard runner artifact list` for what this chunk has already declared.
Commits you cannot account for are never reset, discarded, or force-pushed over — ask
`blizzard runner ask "<question>"` instead of proceeding past them.

Drafts and working notes go somewhere disposable — outside every repository working tree and outside the workspace
directory the fleet spawned you in, since both are git working trees and nothing sweeps a loose file from either. A
per-chunk directory under the machine's temporary space named with `$BLIZZARD_CHUNK_ID` satisfies that, unless this
workspace declares a scratch location of its own, which is preferred.

## Push and declare the commits

Push the branch to each repo's origin. For every repo you touched, you MUST then run
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`; the declaration is mandatory, and an
undeclared push does not count. Re-declaring a tip that was already declared is harmless, so declare again rather than
assuming an earlier attempt's declaration got there.

## Submit the refutation record

On every pass through this node you MUST run `blizzard runner artifact create --name review-finding-refutes` with the
refutation content on stdin; the submission is mandatory, including a first pass that has nothing to refute — say so
explicitly rather than skipping the submission. Read the previous submission first with
`blizzard runner artifact get review-finding-refutes --content`, absent on this node's first-ever visit in the chunk
— normal, not a gap to flag — and carry it forward when present. Every refutation still standing is restated in each
new submission, including any a reviewer already accepted in an earlier round, each marked `open` or `accepted`: that
asset is replaced rather than appended to, and the reviewer sees only the newest submission and never looks for an
older one.
