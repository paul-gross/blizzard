---
kind: added
surfaces: [cli]
---

Adds `blizzard hub backlog get`, `set` and `move`, which read and reorder the not-ready backlog from a terminal the way
`hub queue` does for the ready queue. They never promote or demote a chunk, and a chunk that has become ready is refused
rather than moved.
