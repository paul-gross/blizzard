---
kind: added
surfaces: [config, cli]
---

A runner can now pace its spend, cap the utilization of its subscription's windows, and keep to a schedule. Three
optional tables under `[[runner]]` in `blizzard-host.toml` set them: `[runner.throttle.pace]` (with optional
`[[runner.throttle.pace.taper]]` steps), `[runner.throttle.utilization]`, and `[runner.throttle.schedule]`. A runner
asks the subscription floor and then each configured throttle before it claims work or starts a step, and waits while
any says no; a step already begun is not stopped. `blizzard runner status` and the runner panel show each throttle's
setting, reading, and when it next admits. A runner with no `[runner.throttle]` behaves as before.

## Upgrade

Add the tables to a runner's entry and restart the host. An older wheel ignores `[runner.throttle]`, so remove it before
rolling back or the runner runs unthrottled.
