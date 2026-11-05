---
kind: removed
surfaces: [trace]
retires: deprecate-run-step-span-attributes
breaking: true
upgrade-guide: runners-below-04-are-refused
---

The `blizzard.hub.run_step.name` and `blizzard.hub.run_step.exit_code` span attributes, deprecated and no longer emitted
since 0.3, leave the trace dictionary, and the trace schema and the hub's and runner's fleet scope versions rise to 4.

## Upgrade

Read the `hub action pass` span's `blizzard.hub.action.name` and `blizzard.hub.action.choice` instead. Redeploy the hub
and every runner so their spans carry schema 4; no span id changes and nothing needs a replay.
