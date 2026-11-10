---
kind: added
surfaces: [http-api, wire, cli, config, store]
---

Lets a mint state a chunk's urgency, and gives routines and chores urgencies of their own. A new chunk takes the first
urgency its chain names: the one the mint states, then its chore's override, then its routine's default urgency, then
the tenant's default. `chunk ingest`, `item create`, `routine run`, and `garden-proposal accept` take `--urgency`, and
their requests `urgency`, by id or slug; one naming no urgency is refused `422 urgency_unknown`, and a retired one
`422 urgency_retired`. A routine's `default_urgency` and a chore's `urgency` are set over the API, by
`routine create`/`routine edit --default-urgency` and `cadence chore add`/`chore edit --urgency`, and in a configuration
document by slug, where each must name a live urgency stored or declared in the same document. A chunk's urgency is
edited through `PATCH /api/chunks/{id}` and `chunk set --urgency` while the chunk is not done or stopped; the edit
leaves its rank alone. Every chunk view carries `urgency` as `{urgency_id, slug}`, and `chunk list`, `queue get`, and
`chunk get` show it. Retiring an urgency clears every routine default and chore override naming it.

## Upgrade

Run `blizzard hub migrate` before starting the upgraded hub. It adds the routine's default urgency and the chore's
urgency, empty on every stored routine and chore.
