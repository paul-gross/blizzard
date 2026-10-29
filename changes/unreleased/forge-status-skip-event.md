---
kind: added
surfaces: [http-api]
---

A forge-status pass that skips a work source because it cannot read the source's forge labels now records a
`forge-status-source-skipped` warning in that tenant's event log, naming the source and its id. It is announced once
per source per outage — on the first skipped pass since the source was last read or the hub started — so a long outage
writes one entry, not one per pass; a successful read re-arms it.
