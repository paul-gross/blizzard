---
kind: changed
surfaces: [http-api, config, cli]
breaking: true
---

A work source's web origin now belongs to its forge, and a work source can no longer hold a web origin of its own. A
work source read returns its forge's API origin as `api_base` — `https://api.github.com` where none was stored — and its
forge's web origin as `web_base`, derived from the API origin when the forge declares none. A write sending a `web_base`
other than its forge's web origin — on `POST` or `PATCH /api/work-sources`, in a configuration document's `work_sources`
entry, or through `blizzard hub source create|edit --web-base` — is refused `422 forge_conflict`, naming the forge and
its web origin. A `web_base` sent as null or empty, or cleared with `--clear web-base`, no longer clears anything: it
means the forge's web origin and is answered with a warning.

## Upgrade

Drop `web_base` from work-source writes, configuration documents, and runbooks, or send the forge's own web origin,
which a work source read returns as `web_base`. A forge's web origin is the one the migration carried over for its
server, or the one declared by the write that made the forge.
