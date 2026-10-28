---
kind: added
surfaces: [http-api]
---

The runner's `GET /runners/{runner_id}/api/escalations` takes an optional `state` of `open` (the default, unchanged) or
`closed`, and a `limit` for the closed page. `state=closed` lists the escalations the hub ended, newest closure first,
one per chunk. Every item gains a `closure` of `{reason, closed_at}` — the `stopped` or `done` the hub ended the chunk
with and when the runner recorded it — which is `null` on an open escalation.
