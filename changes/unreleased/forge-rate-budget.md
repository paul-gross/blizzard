---
kind: added
surfaces: [http-api, wire, cli]
---

The hub now shares one rate budget per forge and secret across every caller of that forge, learned from the forge's
`X-RateLimit-*` headers. As the budget falls, the work-source sweeps and the commit check are turned away before a
request is sent while landings keep going, and a close the budget turns away waits for the reset without spending its
backoff. `GET /api/forges/{forge}` answers each budget's last reading as `budgets`, `blizzard hub forge show` prints it,
and the event log gains `forge-budget-low` and `forge-rate-limited` (warning), each recorded once per reset window.
