---
kind: deprecated
surfaces: [http-api, cli]
removes-in: "0.6"
---

The `by`, `answered_by` and `resolved_by` fields that name an actor on hub requests, and the `--by` option of the
`blizzard hub` verbs that filled them (not `finding supersede --by`), are deprecated. Each is still accepted, `--by`
with a warning, and ignored: the hub records the signed-in caller. Stop sending them.
