---
kind: added
surfaces: [http-api, wire, cli]
flag: cadences
---

Shows a cadence's errands. `GET /api/cadences/{cadence_id}/errands` and `blizzard hub cadence errands` list each chore
over each scope with its state (`due`, `running`, `waiting`, or `stopped`), the attention that stops it, its last run,
and `due_at`, the instant it next comes due. A cadence's view and `cadence get` count its errands by state in
`errand_counts`. A run's list and detail views gain `cadence`, naming the cadence and chore that minted it; the chore's
key is empty once the chore has been removed.
