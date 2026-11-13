# Local hub-action routing recipe

Run only in a provisioned feature environment. This recipe runs the real hub and a stepped mock runner against the local
actions profile, whose `BLIZZARD_TEST_ACTIONS` and `BLIZZARD_CRASH_FENCE` are installed by the harness. It never uses
the hosted hub. The invalid-edge case owns a fresh hub; `breakActionEdge` refuses a shared hub and changes exactly one
edge in that disposable hub's SQLite store. This is deliberately a stored-data fixture: graph creation rejects an edge
to a missing node. Do not point it at a real hub or database.

From the blizzard checkout, set scratch to the provisioned environment's approved directory, then run:

```sh
export TMPDIR="${BLIZZARD_TMPDIR:?set BLIZZARD_TMPDIR to your approved scratch directory}"
mise run playwright -- --project=service-fleet-actions hub-actions.spec.ts
```

The executable recipe is `tests/service/fleet/actions/hub-actions.spec.ts`. The harness starts the fenced hub and local
forge, creates a tenant and a stepped runner, and tears them down. Its graph mints a runner `build` leading to a hub
`test.answer` action. The runner is driven until the read-back condition holds. The `triage` case reads the destination
graph ID and node from chunk detail; `absent` reads the migration-target escalation and verifies there is no unroutable
artifact. For the invalid-edge case the helper changes `deliver`'s `done` edge to `nowhere` before the chunk is
promoted. The spec drives the chunk, reads its status, node and artifacts, then reads `/api/events` and an SSE barrier:
one `hub-node-unroutable-outcome` event row and one corresponding published frame must exist. Playwright reports every
read-back as a passing or failing assertion; it retains hub and runner logs on failure.
