---
kind: fixed
surfaces: [board]
---

A chunk on a follow-latest graph now moves onto the newest mint when a hub-executed node exits to another node, as it
already did when a runner node completes. Before, a chunk that sat at a hub node such as the merge step when a newer
mint arrived carried on along the old definition. The move shows on the chunk's history as a follow-latest migration.
