---
kind: removed
surfaces: [store, wire, trace]
retires: deprecate-hub-exec-slot
breaking: true
breaks:
  - hub_exec_slot
upgrade-guide: runners-below-04-are-refused
---

The hub drops the `hub_exec_slot` table and its rows, and no longer reads them. A hub step run before turns loses its
slot-keyed `hub exec` intervals from its trace. The hub↔runner floor rises to 0.4, so the hub refuses a runner below it.

## Upgrade

Move every runner host onto 0.4 or later before the hub takes this release; the hub migration drops `hub_exec_slot`.
