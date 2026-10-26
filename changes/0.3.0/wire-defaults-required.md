---
kind: changed
surfaces: [http-api]
---

Response schemas in the hub and runner OpenAPI documents now list every field the server always sends as required, a
defaulted field included, so a generated client types it as present rather than optional.
