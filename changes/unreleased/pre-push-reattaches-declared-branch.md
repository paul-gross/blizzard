---
kind: fixed
---

A chunk in `bas-hwf` or `bas-dwf` no longer reaches `pre-push` on a detached HEAD: `review` now confirms each repo's
branch tip against the declared commit instead of checking the commit out. `pre-push` in `bas-hwf`, `bas-dwf` and
`adv-dwf` now puts each repo back on the declared branch with its upstream connected before rebasing, whatever state it
arrives in, and pushes to the declared branch on origin by name.

## Upgrade

Re-mint the packaged graphs with `blizzard hub graph sync`. A chunk already pinned keeps its version.
