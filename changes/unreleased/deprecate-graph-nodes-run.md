---
kind: deprecated
surfaces: [store]
removes-in: "0.4"
---

The hub store's `graph_nodes.run` column is deprecated. The hub no longer reads or writes it: a node minted from here
stores no `run`, a carried node is taken by its `action`, and a command the carry-over could not translate is kept in
`graph_nodes.legacy_run`.
