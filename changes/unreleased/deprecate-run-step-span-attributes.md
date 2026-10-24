---
kind: deprecated
surfaces: [trace]
removes-in: "0.4"
---

The `blizzard.hub.run_step.name` and `blizzard.hub.run_step.exit_code` platform span attributes are deprecated, and the
`hub run step` span that carried them is no longer emitted. Read the `hub action pass` span's `blizzard.hub.action.name`
and `blizzard.hub.action.choice` instead.
