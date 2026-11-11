---
kind: deprecated
surfaces: [http-api]
removes-in: "0.6"
---

The `x-blizzard-landing-policies` key on `GET /api/config/schema/repositories` is deprecated. Read
`GET /api/config/landing-policies` for what each policy needs, and `landing_policies` on a forge's detail for whether
the forge can honour it.
