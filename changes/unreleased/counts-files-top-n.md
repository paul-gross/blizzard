---
kind: added
surfaces: [http-api, cli]
---

`GET /api/analytics/counts/files` takes an optional positive `limit` that returns only the top paths by count, with
complete counts, and `blizzard hub analytics summary counts-files --limit N` opts into it. Without it the read still
returns every path.
