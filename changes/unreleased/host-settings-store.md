---
kind: changed
surfaces: [config, store, cli, http-api]
upgrade-guide: host-settings-move-to-the-store
---

The runner host's live top-level settings — `tick_seconds`, `[transcripts]`, `[worker_stdout]`, the live `[tracing]`
keys and `[tools]` — now persist in a host settings record in the runner store, seeded from the host file, and can be
read and changed with `blizzard runner settings --host` and the `/api/host/settings` routes. Each pass reads them at its
start, and a change to `tick_seconds` or `tracing.sweep_seconds` takes hold at once, without waiting out the current
interval.

## Upgrade

Move `BZ_RUNNER_TICK_SECONDS` into the host file as `tick_seconds`, and after the upgrade change host settings through
the CLI or API rather than the file. Follow the linked upgrade guide for the seeded tools profile and rollback.
