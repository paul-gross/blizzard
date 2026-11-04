---
kind: added
surfaces: [cli]
---

`blizzard runner opencode attend SESSION_ID` hands an OpenCode 2 session to the operator: it interrupts the session's
tree, best effort, replaces the session's rules with the attended set, and opens it with `opencode --session`. It is
the takeover command the OpenCode 2 binding composes; the runner does not dispatch OpenCode 2 turns through that
binding yet.
