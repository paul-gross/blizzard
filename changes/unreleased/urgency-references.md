---
kind: added
surfaces: [http-api, wire, cli, config, store]
---

A mint can state a chunk's urgency (`--urgency` on `chunk ingest`, `item create`, `routine run`, and
`garden-proposal accept`), routines and chores carry urgencies of their own, and a new chunk takes the first urgency
that chain names before the tenant's default. An operator can edit an open chunk's urgency with `chunk set --urgency`,
and every chunk view, `chunk list`, `queue get`, and `chunk get` show it.

## Upgrade

Run `blizzard hub migrate` before starting the upgraded hub. It adds the routine's default urgency and the chore's
urgency, empty on every stored routine and chore.
