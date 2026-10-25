---
kind: added
surfaces: [config, http-api]
---

Each hub sweep can be stopped and run a pass at a time. A `[sweeps.<name>]` table in `blizzard-hub.toml` sets a sweep's
`mode`: `timed`, the default, runs it on its timer as before, and `stepped` starts no timer.
`POST
/api/sweeps/{name}/passes` runs one `forge_status`, `event_derivation` or `close_drain` pass over the request's
tenant, for a tenant admin; `POST /api/admin/sweeps/{name}/passes` runs one pass of any sweep over every tenant, for a
hub administrator. Each answers once its pass has finished, with `409 {"code": "sweep_in_progress"}` while another pass
of the sweep holds its lock.

## Upgrade

Nothing is needed to take the release; with no `[sweeps]` table every sweep runs on its timer as before. An older hub
ignores `[sweeps]`, so going back puts a stepped sweep on its timer again.
