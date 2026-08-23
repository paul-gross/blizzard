# Build

You work this prompt at a chunk's `build` node-step. The chunk wraps one or more work items: read them with
`blizzard runner work-items <chunk-id>` before implementing the change in the leased environment(s). This lane's subject
is harness work — agent-facing conventions, skills, prompts, and docs — the rules agents operate under rather than
application behavior.

No planning node precedes this one: an approach that needs working out is thought through inline while building, and no
plan artifact is produced or gated on. Build and verification are fused into this one node, with no verify node behind
it, so the work is validated against the work item's intent here, before done is declared.

Declare done only once every condition the rest of this prompt states holds.

## Orient before changing anything

Take nothing an earlier step left behind for granted — a worker can arrive here from any direction. In each repo you
expect to touch, check which branch is checked out, whether the working tree is clean, and what the branch carries
beyond the base branch, and run `blizzard runner artifact list` for what this chunk has already declared and which
assets arrived with it.

Commits you cannot account for are never reset, discarded, or force-pushed over. A branch holding unexplained work stops
you: ask `blizzard runner ask "<question>"` rather than proceeding.

## Build the change

The work exists as commits on one feature branch named `feat/<slug>`, a short kebab-case slug derived from the work
item, and the same branch name is used in every repo the change touches. Before the first commit, each repo you touch is
on that feature branch and arranged so a push from this environment reaches the feature branch and not the base branch
it started on.

Drafts and working notes go somewhere disposable — outside every repository working tree and outside the workspace
directory the fleet spawned you in, since both are git working trees and nothing sweeps a loose file from either. A
per-chunk directory under the machine's temporary space named with `$BLIZZARD_CHUNK_ID` satisfies that, unless this
workspace declares a scratch location of its own, which is preferred.

Harness work is validated by reading the change back as the agent who will receive it: does the rule say what it means,
does the routing to it land, and does the instruction survive being followed literally?

## Push and declare the commits

Push the branch to each repo's origin. For every repo you touched, you MUST then run
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`; the declaration is mandatory, and an
undeclared push does not count. Re-declaring a tip that was already declared is harmless, so declare again rather than
assuming an earlier attempt's declaration got there.

## Submit the refutation record

On every pass through this node you MUST run `blizzard runner artifact create --name review-finding-refutes` with the
refutation content on stdin; the submission is mandatory. Read the previous submission first with
`blizzard runner artifact get review-finding-refutes --content` and carry it forward. Every refutation still standing is
restated in each new submission, including any a reviewer already accepted in an earlier round, each marked `open` or
`accepted`: that asset is replaced rather than appended to, and the reviewer sees only the newest submission and never
looks for an older one.
