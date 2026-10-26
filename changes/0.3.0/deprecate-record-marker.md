---
kind: deprecated
surfaces: [cli]
removes-in: "0.4"
---

`blizzard hub record-marker` is deprecated, and refuses. Only a hub node's `run:` script ran it, and a hub step no
longer runs commands, so it records nothing: it exits non-zero, naming its removal in 0.4. A hub node's action records
its own markers.
