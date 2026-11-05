---
kind: added
surfaces: [http-api, config, cli, store]
flag: cadences
---

Adds cadences: a project's stored rhythm — a cron schedule, a spacing, or both — and the chores it keeps, each a routine
over the scopes it names. They are created, edited, retired, and re-enabled over `/api/cadences` and
`/api/projects/{project}/cadences`, through `blizzard hub cadence` and its `chore` verbs, and in a configuration
document's `cadences` section. Each cadence's view lists what needs attention before its errands could run. A cadence
mints nothing while the `cadences` flag is off.
