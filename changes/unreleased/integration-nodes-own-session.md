---
kind: changed
---

The packaged `pre-push` and `resolve` nodes now each start a fresh session. They no longer resume the build session, so
a chunk reaching either node does not carry the build conversation's history into it.
