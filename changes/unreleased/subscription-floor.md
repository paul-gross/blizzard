---
kind: changed
surfaces: [cli, http-api, store]
upgrade-guide: usage-limit-brake-carry-over
---

A usage limit no longer engages the runner's brake. It marks the subscription the runner spends, and every runner of the
host spending it — no other — waits behind that subscription's floor, which opens when a sample shows the limited window
turned over, or at its expected reset where nothing can be sampled, and an hour past it when no sample comes. The
limited lease resumes on its own once the floor opens. `blizzard runner clear-floor` opens a floor by hand, through
`POST /api/host/subscriptions/{slug}/floor/clear` or a runner's `POST /api/runner/floor/clear`.

## Upgrade

A runner a usage limit braked before this release stays braked until `blizzard runner start --runner <id>` clears it.
