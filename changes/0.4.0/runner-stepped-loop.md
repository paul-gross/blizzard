---
kind: added
surfaces: [config, cli, http-api]
---

A runner host can be stopped and run a pass at a time. A top-level `loop` key in `blizzard-host.toml` is `timed`, the
default, which runs every pass on its timer as before, or `stepped`, which starts no timer: the local API is served and
startup reconciliation, the host's first report and shutdown marking all run, and a pass runs only when requested.
`blizzard-runner host --loop timed|stepped` overrides the file for one run. `POST /runners/{runner_id}/api/ticks` runs
one tick of a runner, with one trace sweep after it when tracing is on, and `POST /api/host/ticks` runs one pass of the
host's own work. Both serve a timed host too, are on the human lane, and answer once their pass has finished, with
`409 {"code": "tick_in_progress"}` while another pass of the same lock is in progress.

## Upgrade

Nothing is needed to take the release; with no `loop` key a host runs every pass on its timer as before. An older host
ignores `loop`, so going back puts a stepped host on its timers again.
