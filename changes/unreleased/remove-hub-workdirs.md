---
kind: removed
surfaces: [config]
retires: deprecate-hub-workdirs
breaking: true
breaks:
  - data/hub_workdirs
upgrade-guide: runners-below-04-are-refused
---

Removes the hub's `data/hub_workdirs` directory, which held the scratch clones a hub `run:` step used and which nothing
has written since 0.3. The hub now removes the directory on boot, and the durable-paths document no longer lists it.

## Upgrade

Nothing to do: the hub deletes `data/hub_workdirs` when it starts, and a backup can stop carrying it.
