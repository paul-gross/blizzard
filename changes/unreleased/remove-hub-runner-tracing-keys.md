---
kind: removed
surfaces: [config]
retires: hub-refuses-runner-tracing-keys
breaking: true
upgrade-guide: runner-only-tracing-keys-in-the-hubs-configuration
---

The hub now refuses the `[tracing]` keys `worker_programs`, `worker_program_services` and `harness_telemetry`. They
configure the runner's receiver, which the hub does not have. The hub's configuration contract still declares them,
deprecated, naming their home in `blizzard-host.toml`, and a `blizzard-hub.toml` that carries one fails the hub's config
load with an error naming that home.

## Upgrade

Delete the keys from `blizzard-hub.toml`'s `[tracing]` table, or move them to `blizzard-host.toml` where the runner
reads them.
