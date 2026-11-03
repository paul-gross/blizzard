---
kind: changed
surfaces: [config]
postpones: hub-refuses-runner-tracing-keys
removes-in: "0.5"
---

The hub's `[tracing]` keys `worker_programs`, `worker_program_services` and `harness_telemetry` stay deprecated through
0.4 and are now removed in 0.5, together with the other deprecations 0.3 and 0.4 carry, so the skew window's floor rises
once.
