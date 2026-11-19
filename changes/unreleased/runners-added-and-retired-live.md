---
kind: changed
surfaces: [cli, http-api, store]
upgrade-guide: runners-become-records-in-the-host-store
---

A host's runners are records in its store, and a running host adds and retires them live. `POST /api/host/runners` adds
a runner under the host's token and the host starts it at its next tick; `POST /api/host/runners/{runner_id}/retire`
retires one, finally, and the host drains it at its next tick while the others keep ticking. `blizzard runner add` and
`retire` call those routes on a running host and write the store directly on a stopped one, saying which; neither writes
`.env` or the host file any more. A `[[runner]]` removed from the file no longer retires its runner, a `[[runner]]` of a
retired runner is warned of and not read, and `GET /api/host/runners` lists `starting`, `start_failed` and `retired`
runners.

## Upgrade

Walking back to an earlier release strands each runner added through the API or `runner add` since: retire it at the hub
and add it again, or write its `[[runner]]` and `BZ_HUB_TOKEN_<NAME>` back by hand first.
