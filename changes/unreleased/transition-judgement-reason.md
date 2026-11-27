---
kind: added
surfaces: [wire, http-api, store, cli]
---

Worker judgement explanations now travel with completions and are stored on transitions. The chunk detail API and
`blizzard hub chunk get` expose the recorded reason when present; older transitions and judgements without an
explanation have no reason.
