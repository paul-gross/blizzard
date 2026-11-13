---
kind: added
surfaces: [http-api]
---

A runner host's `GET /api/health` now answers with the host's hub-minted `host_id` (`null` until its first answered
report) and admits cross-origin reads from the origin of its `hub_url`, so the board can tell which of a host's declared
origins is reachable as that host.
