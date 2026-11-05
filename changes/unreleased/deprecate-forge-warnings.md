---
kind: deprecated
surfaces: [http-api]
removes-in: "0.6"
---

The `warnings` list on the repository and work-source responses and on the configuration apply response is deprecated.
No write sends a forge address field any longer, so the list is always empty; stop reading it.
