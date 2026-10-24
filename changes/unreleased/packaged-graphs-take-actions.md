---
kind: changed
---

The packaged development and garden graphs deliver through actions: each land node takes `land`, `record-findings` takes
`record-findings`, and the garden `deliver` nodes take `garden-deliver`. A hub node with no `poll_timeout` now waits
three hours, where it waited thirty minutes, and the packaged land nodes set three hours too. After moving the hub, an
operator re-mints the five packaged graphs with `blizzard hub graph sync`; a chunk already pinned keeps its version.
