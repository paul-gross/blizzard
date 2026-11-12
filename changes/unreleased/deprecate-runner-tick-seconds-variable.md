---
kind: deprecated
surfaces: [config]
removes-in: "0.6"
---

`BZ_RUNNER_TICK_SECONDS` is deprecated; set `tick_seconds` in `blizzard-host.toml`. The host honors the variable only
when the file states no `tick_seconds`, and warns at start whenever it is set, saying whether it was honored or
overridden.

## Upgrade

Move the value of `BZ_RUNNER_TICK_SECONDS` into the host file as `tick_seconds = <seconds>` and unset the variable. A
host that still sets it starts with a deprecation warning.
