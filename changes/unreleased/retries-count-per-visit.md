---
kind: changed
surfaces: [wire]
---

A node's retry budget now counts per visit to the node, where it counted across the whole chunk, so a chunk that returns
to a node starts that visit with its full budget. The runner now refuses an asset declaration made after the node-step's
outcome is submitted; declare assets before the verdict.
