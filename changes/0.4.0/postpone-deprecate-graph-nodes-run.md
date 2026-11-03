---
kind: changed
surfaces: [store]
postpones: deprecate-graph-nodes-run
removes-in: "0.5"
---

The hub store's `graph_nodes.run` column stays deprecated through 0.4 and is now removed in 0.5, together with the other
deprecations 0.3 and 0.4 carry, so the skew window's floor rises once.
