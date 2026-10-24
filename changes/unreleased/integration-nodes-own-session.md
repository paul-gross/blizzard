---
kind: changed
---

The packaged `pre-push` and `resolve` nodes now run in their own session, where they re-read the whole build
conversation before. A chunk reaching either node no longer inherits the build session's history.
