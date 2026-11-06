---
kind: added
surfaces: [config, http-api, store]
flag: cadences
---

Adds the `cadence` sweep, which mints a run for each errand of a live cadence that comes due: a chore over one scope
whose schedule's moment has passed, or whose spacing has run out, since its last run, and whose last run has settled. A
moment missed while the hub was down is owed one run, however many passed. A promoting chore's run lands at the tail of
the ready queue; any other is held. The sweep passes every 60 seconds, takes a `[sweeps.cadence] mode`, and is served by
`POST /api/sweeps/cadence/passes` for one tenant. A minted run is marked with its cadence and chore, and a routine's
scope link records when it was made. While the `cadences` flag is off, a pass judges its errands and mints nothing.
