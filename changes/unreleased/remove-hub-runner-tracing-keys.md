---
kind: removed
surfaces: [config]
retires: hub-refuses-runner-tracing-keys
breaking: true
breaks:
  - tracing.worker_programs
  - tracing.worker_program_services
  - tracing.harness_telemetry
upgrade-guide: runner-only-tracing-keys-in-the-hubs-configuration
---

Removes the `[tracing]` keys `worker_programs`, `worker_program_services` and `harness_telemetry` from the hub's
configuration contract. They configure the runner's receiver, which the hub does not have. A `blizzard-hub.toml` that
still carries one fails the hub's config load with an error naming `blizzard-host.toml`, where they belong.

## Upgrade

Delete the keys from `blizzard-hub.toml`'s `[tracing]` table, or move them to `blizzard-host.toml` where the runner
reads them.
