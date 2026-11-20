---
kind: added
surfaces: [http-api, cli, store]
upgrade-guide: workspaces-and-subscriptions-become-records-in-the-host-store
---

A host's workspaces and subscriptions are records in its store, seeded once from the host file's `[[workspace]]` and
`[[subscription]]`, and a running host takes each change up at its next tick with no restart.
`blizzard runner workspace` and `blizzard runner subscription` create, list, get, edit, retire and enable them, through
a running host's routes or into a stopped host's store, saying which. The routes are `POST /api/host/workspaces` and
`POST /api/host/subscriptions`, `GET` and `PATCH` (under `If-Match`) on `/api/host/workspaces/{workspace_id}` and
`/api/host/subscriptions/{slug}`, and `POST …/retire` and `POST …/enable`; each write needs superuser authority.
`GET /api/host/workspaces` and `GET /api/host/subscriptions` add each record's `revision`, `state` and settings, and
`?retired=true`. The workspace prompt routes and the `blizzard runner prompt` verbs read and write the workspace
record's prompt, and stay open to any signed-in panel user.
