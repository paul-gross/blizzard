---
kind: fixed
surfaces: [http-api]
---

A gate decision now belongs to its node visit rather than to the claim that opened it. A chunk detached or released at a
gate keeps its decision: the hub refuses a plain completion and a second gate submission while that visit's decision is
unconsumed, and accepts the resolving completion from whichever runner holds the chunk now, at its own epoch. A runner
that claims such a chunk carries the decision on without rerunning the step, whatever its own `gates` are; before, a new
claimant reran the worker and could open a second gate.

During an upgrade the mixed versions stay safe but do not recover on their own. A runner older than this release,
against an upgraded hub, reruns the step: each rerun's completion or gate submission is refused, and after the node's
retries the chunk escalates to needs-human. An upgraded runner against an older hub holds the chunk without rerunning
it, but its resolving completion is refused. Recover either by upgrading the other side or by restarting the chunk,
which closes the decision. No wire or version-floor change.
