---
kind: added
surfaces: [http-api, cli]
---

A runner host applies and exports one settings document shaped like `blizzard-host.toml`: its host-level live keys and
its `[[runner]]`, `[[workspace]]` and `[[subscription]]` tables. `POST /api/host/settings/apply` reconciles every entry
the document states in one transaction, refuses the whole document on any refused entry, and previews with
`?dry_run=true`; `GET /api/host/settings/export` answers every entry not at its default. The CLI gains
`blizzard runner settings apply FILE [--dry-run]` and `blizzard runner settings export`.
