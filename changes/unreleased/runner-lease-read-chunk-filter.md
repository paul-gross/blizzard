---
kind: added
surfaces: [http-api]
---

The runner's `GET /api/leases` takes an optional `chunk_id` that returns every lease of that chunk, open and closed,
oldest first, with no cap on how many. Each lease now also carries its resolved model and effort, the harness version
its newest spawn recorded, and the git commits its worker declared, newest per environment and repo.
