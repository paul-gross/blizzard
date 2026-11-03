---
kind: added
surfaces: [config]
---

`blizzard-host.toml` accepts a `[tools]` table with `path_prepend` and `env_passthrough`: the profile the host's own
children run under, such as workspace provisioning and credential renewal. Without it the host takes the runners'
`[runner.worker]` values when every runner agrees on them, so a host whose runners already agree loads unchanged. A host
whose runners disagree and that sets no `[tools]` is refused by `runner host --check` and by start, naming `[tools]`.
