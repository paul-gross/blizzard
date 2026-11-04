---
kind: added
surfaces: [cli]
---

`blizzard runner opencode compatibility` now proves an OpenCode 2 binary against the operator's own running OpenCode
service, and adds `--opencode1-binary` and `--opencode1-model` to check that a store OpenCode 1 wrote survives OpenCode
2 opening it. A version outside every admitted range is now refused before anything runs, with no evidence written.
