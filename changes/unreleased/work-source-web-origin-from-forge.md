---
kind: changed
surfaces: [http-api]
---

A work source's web origin now belongs to its forge. A work source read returns its forge's API origin as `api_base` —
`https://api.github.com` where none was stored — and its forge's web origin as `web_base`, derived from the API origin
when the forge declares none. A write sending a `web_base` other than its forge's web origin is refused
`422 forge_conflict`, naming the forge; set that forge's web origin, or leave the field out.
