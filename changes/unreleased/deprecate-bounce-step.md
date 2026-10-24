---
kind: deprecated
surfaces: [http-api]
removes-in: "0.4"
---

A `bounce-envelope` artifact's `step` is deprecated. A hub step no longer runs a command, so the hub always writes
`step: null` and names the bounce's `action` instead. Only an envelope stored before this release carries a `step`.
