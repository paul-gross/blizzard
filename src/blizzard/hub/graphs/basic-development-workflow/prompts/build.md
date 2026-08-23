# Build

This is a chunk's build node-step: the chunk wraps one or more work items — read them with
`blizzard runner work-items <chunk-id>` — and you implement the change in the leased environment(s). No planning node
precedes build in this lane: plan inline while building; no plan artifact exists and nothing gates on one. No verify
node follows either — build and verification are one node, and the work must meet the item's intent, verified as far as
this node can verify it, before you declare done.

## Orient first

Entry may be fresh, a retry, or a bounce back from a later node — never assume what an earlier step left; inspect first.
Run `blizzard runner artifact list` to see what is already declared for the chunk and which assets arrived with you. Per
repo you expect to touch, check the checked-out branch, working-tree cleanliness, and what the branch carries beyond the
base branch. Reuse existing correct work rather than redoing it. Never reset, discard, or force-push over commits you
cannot account for; on finding branch work you cannot explain, stop and ask with `blizzard runner ask "<question>"`.

## Branch and commit

Finished work is commits on one branch, `feat/<slug>` — a short kebab-case slug derived from the work item — the same in
every repo changed. Before the first commit, put each touched repo on the feature branch so no push from this
environment can reach the base branch; how you arrange that is the workspace's business, the outcome is mandatory.

Keep drafts and notes outside every repo working tree and outside the spawn workspace directory — both are unswept git
worktrees. Use a temp per-chunk directory named with `$BLIZZARD_CHUNK_ID`, preferring a workspace-declared scratch
location if one exists.

## Push and declare

Push the branch to each changed repo's origin, then declare every touched repo with
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`. `<repo>` is the repo's name in the
environment's repo manifest — never an `owner/name` slug, a path, or a URL; `<sha>` is the full sha, never abbreviated.
Add `--env <id>` when the chunk holds more than one environment. The hub stores only the declared reference and learns
of a push only via declaration — an undeclared push does not count. Re-declaring a declared tip is harmless; declare
again rather than trusting an earlier attempt.

## Refutations

Read the previous refutes submission with `blizzard runner artifact get review-finding-refutes --content` and carry it
forward. Before declaring done, submit the refutation channel:
`blizzard runner artifact create --name review-finding-refutes`, content on stdin. That asset is replaced, not appended
— the reviewer reads only the newest submission — so restate every refutation still standing, previously accepted ones
included, each marked `open` or `accepted`. A bare "nothing to refute" while refutations stand drops them, and the next
cold pass re-raises those findings — write it only when nothing stands.

## Done

Declare done once every condition above holds; the runner then resumes you with the judgement prompt.
