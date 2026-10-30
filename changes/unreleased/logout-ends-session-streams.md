---
kind: security
surfaces: [http-api]
---

`POST /api/auth/logout` now ends every open `GET /api/events/stream` opened under the session it revokes, in every
tenant, after the revocation commits. Before, a logged-out session's open stream kept receiving events until it
disconnected. The same user's other sessions keep streaming, and reconnecting with the revoked session is refused with
`401`.
