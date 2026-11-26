---
kind: fixed
---

A chunk in `bas-hwf` or `bas-dwf` no longer reaches `pre-release` on a detached HEAD: `review` now confirms each
worktree's branch against the declared commit, restoring one that differs onto the declared branch instead of checking
the bare commit out. `pre-release` in `bas-hwf`, `bas-dwf` and `adv-dwf` now puts each repo back on the declared branch
with its upstream connected before rebasing, whatever state it arrives in, and pushes to the declared branch on origin
by name.

## Upgrade

Re-mint the packaged graphs with `blizzard hub graph sync`. A chunk already pinned keeps its version.
