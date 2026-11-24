---
kind: fixed
surfaces: [http-api]
---

Editing a chunk's graph is now refused when it would leave a standing migration intent naming that same graph, whether
the intent arrives in the same edit or is already on the chunk. Clear the intent in the same edit to re-pin.
