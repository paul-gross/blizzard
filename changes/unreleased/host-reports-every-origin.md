---
kind: added
surfaces: [wire, http-api]
---

A host's report to `POST /api/fleet/host` now carries `urls`, every `public_url` origin in declaration order, and
`GET /api/hosts` serves it as `urls` beside the unchanged canonical `url`. A host that reports no `urls` reads as its
one `url`.
