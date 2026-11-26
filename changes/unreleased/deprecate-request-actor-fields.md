---
kind: deprecated
surfaces: [http-api]
removes-in: "0.6"
---

The `by` field on the hub's chunk, dependency, runner, host, cadence, routine and scope lifecycle requests,
`answered_by` on a question answer, and `resolved_by` on a decision resolution are deprecated. Each is still accepted
and ignored: the hub records the signed-in caller. Stop sending them.
