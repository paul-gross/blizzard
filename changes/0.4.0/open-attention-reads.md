---
kind: changed
surfaces: [http-api, store]
---

The hub returns the complete set of pending decisions and questions to operators. The open-decision read now excludes
historical decisions closed by movement, migration, escalation, or restart facts before hydrating results, keeping the
read's row volume tied to pending gates rather than undecided history.
