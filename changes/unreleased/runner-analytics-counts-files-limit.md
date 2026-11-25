---
kind: added
surfaces: [http-api, wire, cli]
---

`blizzard runner analytics counts files` takes `--limit N` to return only the N most-read paths, and the hub's
`GET /api/fleet/chunks/{id}/analytics/counts/files` takes `limit` as `GET /api/analytics/counts/files` does.
