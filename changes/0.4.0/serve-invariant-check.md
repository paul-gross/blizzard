---
kind: added
surfaces: [http-api]
---

The hub serves `GET /api/admin/invariants` to hub administrators and the runner host serves `GET /api/host/invariants`
on its human lane. Each runs its store's invariant checks inside one read transaction and answers every breach as
`{invariant, detail}`, or `409 {"code": "invariants_in_progress"}` while another check runs on the same daemon.
