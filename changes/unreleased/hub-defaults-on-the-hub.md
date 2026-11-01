---
kind: changed
surfaces: [cli, http-api]
---

The hub now defaults a new project's name to its slug: `POST /api/projects` accepts a request with no `name`, and
`blizzard hub project|repo|source create` send a name only when `--name` is given. `blizzard hub finding list` no longer
refuses a call without `--scope` or `--routine`; the hub answers it. `blizzard hub queue move` reports the position the
hub landed the chunk at.
