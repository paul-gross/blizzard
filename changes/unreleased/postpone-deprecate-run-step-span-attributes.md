---
kind: changed
surfaces: [trace]
postpones: deprecate-run-step-span-attributes
removes-in: "0.5"
---

The `blizzard.hub.run_step.name` and `blizzard.hub.run_step.exit_code` span attributes stays deprecated through 0.4 and
is now removed in 0.5, together with the other deprecations 0.3 and 0.4 carry, so the skew window's floor rises once.
