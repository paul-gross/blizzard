---
kind: added
surfaces: [http-api]
---

Adds `GET /api/config/landing-policies`, listing every landing policy with the forge capabilities it requires and the
ones it uses, and `landing_policies` on a forge's detail, saying for each policy whether the forge can honour it and
which required capabilities it lacks.
