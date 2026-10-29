---
kind: deprecated
surfaces: [store]
removes-in: "0.4"
---

The hub store's `hub_exec_slot` table is deprecated. Hub steps take turns on their flows, recorded in `hub_turns`, and
nothing writes the slot's table; its rows stay readable as the history of hub steps run before turns, and the trace
corpus still reads them for those steps' `hub exec` spans.
