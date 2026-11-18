---
kind: fixed
surfaces: [cli]
---

`blizzard hub egress reset --help` no longer promises that a forward reset prevents all exports of the skipped window: a
pass already in progress may still write files for it.
