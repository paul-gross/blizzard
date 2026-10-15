# Build

This is a chunk's build node-step: read the chunk's work items with `blizzard runner work-items <chunk-id>` and
implement the change in the leased environment. Plan inline; no plan artifact exists and no verify node follows, so the
work must meet the item's intent, verified as far as this node can, before you declare done.

## Orient first

- Entry may be fresh, a retry, or a bounce from a later node: run `blizzard runner artifact list` to see what is already
  declared and which assets arrived with you.
- Per repo you expect to touch, check the branch, working-tree cleanliness, and what it carries beyond the base branch.
  Reuse correct work rather than redoing it.
- Never reset, discard, or force-push over commits you cannot account for; ask with `blizzard runner ask "<question>"`.

## Branch and commit

Finished work is commits on one branch, `feat/<slug>` — a kebab-case slug from the work item — the same in every repo
changed. Put each repo on that branch before its first commit, so no push can reach the base branch.

## Verify it by hand

Before pushing, exercise the change against a running application — a green build is not a verification. Bring up the
environment's services, confirm readiness, and drive every surface the change reaches through the method the project's
verifiability matrix declares. A change reaching no running surface is exempt. Record what you exercised in the commit
message.

## Push and declare

Push each changed repo's branch, then declare it:
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`.

- `<repo>` is the repo's name in the environment's repo manifest, never a slug, path, or URL; `<sha>` is the full sha.
- Add `--env <id>` only when the chunk holds more than one environment.
- An undeclared push does not count; re-declaring a tip is harmless.

## Refutations

Read the standing refutations with `blizzard runner artifact get review-finding-refutes --content`, then submit them
with `blizzard runner artifact create --name review-finding-refutes`, content on stdin. The submission replaces the last
one, so restate every refutation still standing, each marked `open` or `accepted`; write "nothing to refute" only when
none stands.

## Done

Declare done once every condition above holds; the runner then resumes you with the judgement prompt.
