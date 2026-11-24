---
kind: fixed
surfaces: [board, http-api]
---

A chunk's "target graph names no enabled graph" escalation now names the node and the choice that was taken. A different
choice to the same missing graph is now refused while that escalation is open, instead of being answered as if it were
the one already escalated.
