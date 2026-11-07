---
kind: changed
surfaces: [http-api, cli]
---

Refuses a routine that states no handle with the field-invalid problem. `POST /api/routines`,
`POST /api/projects/{project}/routines`, and `POST /api/config/apply` answer a routine stating neither a slug nor a
name with a 422 naming `slug`. `blizzard hub cadence chore add` with a key the cadence already holds now reports the
hub's refusal.
