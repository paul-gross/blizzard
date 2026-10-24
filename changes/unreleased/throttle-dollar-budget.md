---
kind: added
surfaces: [config]
---

A runner can throttle its starts at a dollar budget below its `[runner.cost]` ceiling. The `[runner.throttle.budget]`
table in `blizzard-host.toml` sets `usd` and, optionally, `window_hours` (default 24); the runner takes new work only
while its spend over the trailing window is under `usd`. A runner declaring no budget behaves as before.

## Upgrade

Nothing is needed to take the release. To go back to an older wheel after setting a budget, remove
`[runner.throttle.budget]` from `blizzard-host.toml` first: the older runner's strict configuration view refuses a table
it does not know.
