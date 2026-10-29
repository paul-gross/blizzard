---
kind: changed
surfaces: [http-api]
upgrade-guide: hub-steps-take-turns-on-their-flows
---

The hub takes every hub step as a turn on the flows the step holds, one per repository branch. Landings onto different
branches run side by side, a chunk's landing no longer needs its runner, and `hub-advance` wakes the hub and answers
`ran: false` with where the visit stands. A step cut off at its deadline three times running escalates with
`hub-action-stuck`, and a chunk paused at a hub node lands only once it is resumed.

## Upgrade

A chunk paused at a hub node when you install this release lands only once someone resumes it, with
`blizzard hub chunk resume <id>`.
