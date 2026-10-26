---
kind: added
surfaces: [cli, http-api, store]
---

A runner now reports which throttle is holding it back, and the hub, the board, and `blizzard hub status` show why an
idle runner is idle. `POST /api/fleet/runners` accepts an optional `declining` list — one member per throttle saying no,
with its `lever`, `detail`, and the `subscription` and `until` it concerns — and the runner views carry it back as
`declining`. A runner that sends none reads as declining nothing.

## Upgrade

Move the hub before the runner host. To go back, downgrade the hub store before installing the older image; a runner
that sends the field to an older hub is ignored.
