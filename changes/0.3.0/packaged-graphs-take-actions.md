---
kind: changed
upgrade-guide: hub-nodes-run-no-commands
---

The packaged development and garden graphs deliver through actions: each land node takes `land`, `record-findings` takes
`record-findings`, and the garden `deliver` nodes take `garden-deliver`. A hub node with no `poll_timeout` now waits
three hours, where it waited thirty minutes, and the packaged land nodes set three hours too. After moving the hub, an
operator re-mints the five packaged graphs with `blizzard hub graph sync`; a chunk already pinned keeps its version.

## Upgrade

Move the hub first, then run `blizzard hub graph sync` to re-mint the five packaged graphs. Run it against the moved hub
only: an older hub reads `action:` as a key it does not know and mints each delivery node as one that does nothing.
Stored nodes need no re-mint; the linked boundary carries them.
