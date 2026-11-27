---
kind: fixed
surfaces: [board, http-api, event-stream]
---

Activity rows show the fact's cause consistently live and after reload. Transition rows include the recorded source and
destination when known; the activity API and chunk-change event carry these as `transition_from` and `transition_to`.
