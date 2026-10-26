---
kind: deprecated
surfaces: [http-api]
removes-in: "0.4"
---

`POST /api/chunks/{chunk_id}/hub-markers`, `POST /api/chunks/{chunk_id}/garden-delivery` and
`POST /api/chunks/{chunk_id}/review-findings-delivery` are deprecated. Only a hub node's `run:` script called them, and
a hub node now records its markers and deliveries through its action. Each now admits only a person holding
`chunk:control`; the `X-Blizzard-Marker-Token` header is no longer honoured, so a request carrying only it is refused
`401`.
