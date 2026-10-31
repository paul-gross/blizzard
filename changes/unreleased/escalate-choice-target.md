---
kind: added
surfaces: [http-api, wire, board]
---

A graph choice may now point at the reserved target `escalate`, a sibling of `done`. Taking it parks the chunk
`needs_human` with an `escalate-choice` escalation instead of recording a transition; a requeue re-runs the node. It is
legal on runner nodes only, and no node may be named `escalate`.
