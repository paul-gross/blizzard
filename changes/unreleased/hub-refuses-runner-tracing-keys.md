---
kind: deprecated
surfaces: [config]
removes-in: "0.4"
upgrade-guide: runner-only-tracing-keys-in-the-hubs-configuration
---

The `[tracing]` keys `worker_programs`, `worker_program_services` and `harness_telemetry` are deprecated in
`blizzard-hub.toml`: they configure the runner's receiver, which the hub does not have. The hub now fails its config
load when one is present, naming `blizzard-host.toml`, where they belong.
