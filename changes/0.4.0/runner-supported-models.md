---
kind: added
surfaces: [config, wire, http-api]
---

A runner binding can declare the native model names it supports with `[runner.models] supported` (Claude Code) or
`[runner.opencode.models] supported` (OpenCode), as exact names or `*` globs. The runner reports the list, and the hub
matches a session that names only native names across several harnesses against it, so such a session no longer waits in
`ready` forever.
