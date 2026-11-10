---
kind: added
surfaces: [http-api, wire]
flag: cadences
---

A routine's view gains `kept_by`: one entry for each chore that keeps the routine on an enabled cadence of its project,
naming the cadence, the chore, and the cadence's next scheduled moment (empty for a cadence with no schedule). A retired
routine is still kept. Every route that serves a routine carries it, so a reader no longer joins cadences itself.
