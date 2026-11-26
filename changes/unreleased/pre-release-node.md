---
kind: changed
surfaces: [board]
---

The packaged graphs' `pre-push` node is now `pre-release`, in `bas-hwf`, `bas-dwf` and `adv-dwf`. Its summary artifact
is now `pre-release-summary`, and `bas-hwf`'s `prepush` session pool is now `prerelease`. The board shades a step at
either name the same.

## Upgrade

Re-mint the packaged graphs with `blizzard hub graph sync`. A chunk already pinned keeps its version and its `pre-push`
node. Do not re-mint while a chunk following its graph's latest version is between `review` and `deliver`, because it
would land on the new version's entry node rather than on `pre-release`. Rename `pre-push` to `pre-release` in any
runner's `gates` or `BZ_RUNNER_GATES`, keeping `pre-push` beside it while chunks remain on an older version; a gate
naming only `pre-push` holds none of the new version's chunks.
