---
kind: deprecated
surfaces: [config]
removes-in: "0.4"
---

The hub's `data/hub_workdirs` directory is deprecated. It held the scratch clones a hub `run:` step used, and no hub
step writes it any more; it holds nothing the store lacks.
