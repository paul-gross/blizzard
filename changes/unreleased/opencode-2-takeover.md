---
kind: added
surfaces: [cli]
---

`blizzard runner opencode attend SESSION_ID [--model PROVIDER/MODEL[#VARIANT]]` hands a fleet OpenCode 2 session to the
operator: it interrupts the session's tree, best effort, replaces the session's rules with the attended set, switches
the session back to the lease's model when it runs another, and opens it in the operator's OpenCode. It refuses a
session no blizzard runner minted. It is the takeover command the OpenCode 2 binding composes.
