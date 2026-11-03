---
kind: changed
---

A rejected garden delivery now returns to the node that drafts the faulty artifact. A delta fault still answers
`invalid` and routes to `reconcile`; a docket fault, or a citation that spans both artifacts, answers the new
`invalid-proposals` and routes to `propose`. Each retry addendum counts rejections of its own artifact only, so a delta
fix followed by a docket fix no longer escalates. A chunk pinned to a garden graph version without the new choice
escalates on a docket rejection.

## Upgrade

Re-mint the packaged graphs with `blizzard hub graph sync`. A deployment's own graph that uses `garden-deliver` must
author an `invalid-proposals` choice, or the mint is refused. A chunk already pinned keeps its version.
