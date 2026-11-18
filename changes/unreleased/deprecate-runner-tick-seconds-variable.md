---
kind: deprecated
surfaces: [config]
removes-in: "0.6"
---

`BZ_RUNNER_TICK_SECONDS` is deprecated; set `tick_seconds` in `blizzard-host.toml`. The host seeds the variable's value
into its settings record only when neither the store nor the file holds `tick_seconds`, and warns at start whenever it
is set, saying whether it was seeded or ignored.

## Upgrade

Move the value of `BZ_RUNNER_TICK_SECONDS` into the host file as `tick_seconds = <seconds>` and unset the variable. A
host that still sets it starts with a deprecation warning.
