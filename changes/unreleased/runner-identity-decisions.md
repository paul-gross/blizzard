---
kind: changed
surfaces: [cli, http-api]
---

A runner's identity refusals now read the same everywhere. `GET /api/host/runners`, the hub proxy's
`runner_identity_mismatch` detail and `blizzard runner status` give one sentence for a token that belongs to another
runner, and `blizzard runner status` prints a line for a runner whose version the hub refuses. The hub proxy's first
identity check answers `runner_token_refused` for a token the hub refuses, and an unreachable hub once its retries are spent. A
hub that refuses a retired host is reported as a retired host. When `blizzard runner init` keeps a runner whose store
names a runner the host does not configure, it advises restoring the right store if the store also names one of the
host's runners, and otherwise restoring that runner's token or resetting the store. Ingest no longer records
the host identity; the host's first answered report does.
