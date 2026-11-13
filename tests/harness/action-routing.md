# Manual local hub-action walk

Use only a **provisioned feature environment**, never the hosted hub. This walk uses the `service-fleet-actions`
profile: its fenced local hub enables `BLIZZARD_TEST_ACTIONS` and `BLIZZARD_CRASH_FENCE`; a local mock forge and stepped
mock runner supply the other actors. Each case uses a fresh tenant; the deliberately corrupt edge uses a disposable
fresh hub. `breakActionEdge` refuses a shared hub and changes one stored edge, because graph creation rejects that edge.
There are no production test endpoints. The executable fixture is `action-routing.ts` and the actors and graphs are in
`tests/service/fleet/actions/hub-actions.spec.ts`.

## Start

From the feature environment's blizzard checkout in a terminal with a controlling TTY:

```sh
export BLIZZARD_TMPDIR=/absolute/path/to/your/approved/scratch
export TMPDIR="$BLIZZARD_TMPDIR"
export BLIZZARD_ACTION_WALK=manual
mise run playwright -- --project=service-fleet-actions hub-actions.spec.ts
```

The command stays in the foreground. At each `ACTION WALK STEP` read the instruction and press Enter **once** to permit
that act. The fixture creates the tenant, graph, chunk, forge and runner, performs the named public API/CLI calls and
steps the runner; this is an operator-gated walk, not a set of commands to type against a hub yourself. Read the
`ACTION WALK <case> { ... }` JSON before allowing the next act. It prints chunk, graph, node, history, escalation,
artifacts, bounces, operational events, flow submissions and turns (with IDs), flow-record facts and landing facts. Each
checkpoint independently calls `blizzard hub chunk get <chunk> --json` and `blizzard hub events --chunk <chunk> --json`,
compares them with the public chunk and event APIs, and reads each flow's record both via
`blizzard hub flow get <flow> --record --limit 200 --json` and `/api/flows/<flow>/record`. CLI flow records may have
newer facts if polling continues; every already-read API fact must appear in the CLI read. The raw turn API is newest
first; history is transition order and the operational event feed is newest first. Empty events are meaningful:
transitions and turns are not necessarily operational event rows. A nonzero Playwright result or failed assertion is a
failed walk. Fixture teardown stops its daemons even when an assertion fails.

## Walk and expected observations

1. **Normal landing.** Permit promotion. At `landed`, expect `build/pass → deliver/landed → done`, one landing fact, a
   `flow-landed` event, a landed submission and turn. Continue to check the mock forge's changed main and merged PR.
2. **Transitions and waiting test action.** Permit promotion. At `transition`, history must be `build/pass → pick`,
   `pick/onward → walk`, then `walk/done → done`. The `walk` submission's newest-first turns show `done` after
   `pending`; the two `test-sequence/` artifacts record `pending` then `done`.
3. **Cross-graph migration.** Permit promotion. At `migration-triage`, the printed destination graph ID and node `build`
   must match the created `triage` graph. The source `deliver` submission closed with `landed`.
4. **Escalation.** Permit promotion. At `migration-absent`, expect `needs_human` at `deliver`, escalation cause
   `migration-target-unresolvable`, an `escalated` turn and a `needs-human` event, with no unroutable artifact.
5. **Unroutable outcome.** The fixture first plants `deliver/done → nowhere` in its own disposable database. Permit
   promotion. At `unroutable`, expect `needs_human`, one `hub-unroutable-outcome` artifact and one matching operational
   row. The newest-first event feed has `needs-human` followed by `hub-node-unroutable-outcome`; their timestamps put
   the unroutable event first chronologically. The subscribed SSE barrier also checks one published unroutable frame.
6. **Conflict bounce.** The fixture arms mock forge `merge_conflict`. Permit promotion. At `merge_conflict`, history is
   `build/pass → deliver/conflict → build`; inspect the conflict turn, bounce envelope, no landing fact, and no landing.
   Continue; the test's tenant and runner are torn down without retrying the build.
7. **Checks wait and release.** The fixture arms mock forge `checks_pending`. Permit promotion. At `checks_pending`,
   expect `delivering` on `deliver`, an open submission and a `pending` turn. Press Enter after inspection, then press
   Enter at the release gate to delete the lever. At `checks_released`, expect `done`, a landing fact, a `flow-landed`
   event, and newest-first turns with `landed` ahead of at least one `pending`. Continue to teardown.

The printed IDs identify this run only. To run unattended, unset `BLIZZARD_ACTION_WALK`; the same fixture runs without
operator gates. Test failures retain hub/runner logs. Do not copy the invalid-edge helper to a shared or real hub.
