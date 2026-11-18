---
kind: changed
surfaces: [config, store, cli, http-api]
upgrade-guide: runner-settings-move-to-the-store
---

Runner settings now persist in the runner store and can be read and changed through the runner settings CLI and API. New
passes and invocations use their respective settings snapshots without changing running work.

## Upgrade

Back up and explicitly migrate each runner store before starting the new release; review first-seeded settings and use
the settings CLI or API for later edits. Follow the linked upgrade guide for host tools, retirement cleanup and
rollback.
