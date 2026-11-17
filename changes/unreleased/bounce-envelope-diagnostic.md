---
kind: changed
surfaces: [http-api]
---

A delivery bounce's `bounce-envelope` now carries a `diagnostic` key: the hub's own account of a bounce no action
answered, or `null` where an action did. The hub no longer repeats the bounce's facts in `detail` as a generated
sentence; `detail` holds the diagnostic, or `""`. Bounces already recorded keep their text.
