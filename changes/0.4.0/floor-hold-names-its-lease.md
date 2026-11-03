---
kind: added
surfaces: [http-api]
---

A usage-limit mark's `throttle.floor.hold` on `GET /runners/{id}/api/runner` and in the per-runner throttle of
`GET /api/host/runners` gains `lease_id`, the lease whose invocation met the limit; a window hold's is `null`.
