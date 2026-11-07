---
kind: added
surfaces: [cli]
---

A runner works OpenCode 2 when its configured `opencode` binary is version 2.x: the binary's `--version`, read once as
the runner starts, chooses the OpenCode 1 or OpenCode 2 binding for the same `[opencode]` section, and any other version
is refused as before. On OpenCode 2 the runner drives every turn on the operator's own managed service, starting it when
none answers, and ignores `auth_path` and `worker_config_path`; it advertises and records the service's version, and a
launch on a service whose version has changed since is refused until the runner observes it afresh. A service seen on
another major version leaves the capability unavailable until the runner restarts; a binary and service of different
major versions leave it unavailable while they differ. A bundle's `opencode/` directory holds nothing an OpenCode 2
runner reads, so a runner on OpenCode 2 refuses to publish one with entries in it. `blizzard runner init` scaffolds
neither the heartbeat plugin nor the worker config for a runner on OpenCode 2, and removes none already written.
