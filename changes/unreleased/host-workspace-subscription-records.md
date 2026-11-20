---
kind: added
surfaces: [http-api, cli, store]
upgrade-guide: workspaces-and-subscriptions-become-records-in-the-host-store
---

A host's workspaces and subscriptions are records in its store, seeded once from the host file, and a running host takes
each change up at its next tick with no restart. `blizzard runner workspace` and `blizzard runner subscription` create,
list, get, edit, retire and enable them — as do superuser `POST`, `PATCH` (under `If-Match`) and `…/retire` and
`…/enable` routes under `/api/host/workspaces` and `/api/host/subscriptions`, whose lists add each record's `revision`,
`state`, settings and `?retired=true`. The workspace prompt routes and `blizzard runner prompt` verbs read and write the
workspace record's prompt, open to any signed-in panel user.
