---
kind: added
surfaces: [trace]
---

The hub writes an unhandled request error to its log as one JSON line naming the tenant, the method and path, and the
exception class, with the traceback alongside.
