# Manual local hub-action walk

Use only a **provisioned feature environment**, never the hosted hub. This walk uses the `service-fleet-actions`
profile: its fenced local hub enables `BLIZZARD_TEST_ACTIONS` and `BLIZZARD_CRASH_FENCE`; a local mock forge and stepped
mock runner supply the other actors. Each case uses a fresh tenant; the unroutable case uses a disposable fresh hub with
a test-only action implementation that returns an unadvertised choice. Its graph is created through the public API and
its store is never modified. There are no production test endpoints. The executable fixture is `action-routing.ts` and
the actors and graphs are in `tests/service/fleet/actions/hub-actions.spec.ts`.

## Start

From the feature environment's blizzard checkout in a terminal with a controlling TTY (for a shell without one, use the
FIFO alternative below):

```sh
export BLIZZARD_TMPDIR=/absolute/path/to/your/approved/scratch
export TMPDIR="$BLIZZARD_TMPDIR"
export BLIZZARD_ACTION_WALK=manual
mise run playwright -- --workers=1 --project=service-fleet-actions hub-actions.spec.ts
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

### No controlling TTY

A single verifier with a non-interactive shell can run the same walk using a private FIFO under its approved scratch
root. Run from the checkout; use a new path for each run, and never share the FIFO between walks:

```sh
export BLIZZARD_TMPDIR=/absolute/path/to/your/approved/scratch
export TMPDIR="$BLIZZARD_TMPDIR" BLIZZARD_ACTION_WALK=manual
export BLIZZARD_ACTION_GATE_FIFO="$BLIZZARD_TMPDIR/action-gate.fifo"
mkfifo "$BLIZZARD_ACTION_GATE_FIFO"
mise run playwright -- --workers=1 --project=service-fleet-actions hub-actions.spec.ts > "$BLIZZARD_TMPDIR/action-walk.log" 2>&1 &
WALK_PID=$!
```

Read `action-walk.log` until the **next** `ACTION WALK STEP` appears, inspect as directed, then send exactly one gate:

```sh
printf '\n' > "$BLIZZARD_ACTION_GATE_FIFO"
```

The write waits until the test opens the next gate; do not write ahead or issue concurrent writes. Repeat through all
cases. After the final gate, `wait "$WALK_PID"` in the launching shell and check its exit status, then
`rm "$BLIZZARD_ACTION_GATE_FIFO"`. If aborting, interrupt the launched process and wait for its teardown before removing
the FIFO; never use a process-name kill. The TTY recipe does not set `BLIZZARD_ACTION_GATE_FIFO`.

## Held runner lease

After each promotion, the fixture steps until the test-owned mock worker is held before the build and prints
`ACTION WALK LEASE` JSON. Its `hub`, `tenant`, `chunk`, `board`, `runner`, `lease` and `leasesUrl` identify this case;
`readback` is the real runner host API's current lease list. Independently GET the printed `leasesUrl` while the gate is
held (no tenant header is needed on this local runner host):

```sh
curl -sS '<printed-leasesUrl>'
```

Match `lease_id` and `chunk_id`, and check `state` is `running` and `closed_at` is null; inspect the matching board as
below. The snippet's HTTP wait has a **300-second timeout**: inspect promptly and press Enter to release the worker. The
host and checkpoint are test-owned and stop at teardown. The runner host is real; the worker executing the build is
mocked. Nothing here is a production test endpoint.

## Inspect the matching board and live API

At each outcome checkpoint, `ACTION WALK INSPECT` prints this case's **hub**, **tenant**, direct **board** and **api**
URLs. They can change at every case: in particular, the unroutable case has a different hub. The hub serves the built
board; do not use the feature environment's normal web server or hub. Open the printed board URL in a browser on the
same machine. In its developer console run the following, substituting the printed tenant ID, then reload the printed
board URL:

```js
sessionStorage.setItem('blizzard.tenant', '<tenant ID>')
localStorage.setItem('blizzard.tenant.last', '<tenant ID>')
```

Confirm the URL's chunk ID matches the printed checkpoint and inspect the chunk detail and its history. Browser storage
is per origin, and the tab's session tenant takes precedence over the browser's last choice. Set both keys for **every**
case, even if reusing the same tab and hub URL; reload to switch tenants. The board may not expose every raw fact. In
another terminal independently read the live public APIs, replacing all placeholders with the current printed values
(flow and submission IDs are in `ACTION WALK` JSON):

```sh
HUB='http://127.0.0.1:<printed-port>' TENANT='<printed-tenant>' CHUNK='<printed-chunk>'
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/chunks/$CHUNK"
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/events?chunk_id=$CHUNK"
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/chunks/$CHUNK/flow-submissions"
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/chunks/$CHUNK/flow-submissions/<submission-id>/turns?limit=200"
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/flows/<flow-id>/record?limit=200"
curl -sS -H "X-Blizzard-Tenant: $TENANT" "$HUB/api/chunks/$CHUNK/landing-facts"
```

Read while the terminal is paused, before pressing Enter: teardown removes the fixture and its hub may stop. Pending hub
actions can continue polling during inspection, so turn/record lists may grow; the checks-pending action has a
600-second poll timeout, so release it before that limit. Use read-only inspection; do not click board controls. Finish
all gates to let Playwright tear down normally; to abort use Ctrl-C and wait for teardown before closing the terminal.
Close the board tab afterwards. If interrupted forcibly, fixture processes may need manual cleanup; never kill processes
by pattern on a shared runner host.

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
5. **Unroutable outcome.** The disposable hub fixture answers `unadvertised` despite its action catalogue and graph
   advertising `landed`. Permit promotion. At `unroutable`, expect `needs_human`, one `hub-unroutable-outcome` artifact
   and one matching operational row. The newest-first event feed has `needs-human` followed by
   `hub-node-unroutable-outcome`; their timestamps put the unroutable event first chronologically. The subscribed SSE
   barrier also checks one published unroutable frame.
6. **Conflict bounce.** The fixture arms mock forge `merge_conflict`. Permit promotion. At `merge_conflict`, history is
   `build/pass → deliver/conflict → build`; inspect the conflict turn, bounce envelope, no landing fact, and no landing.
   Continue; the test's tenant and runner are torn down without retrying the build.
7. **Checks wait and release.** The fixture arms mock forge `checks_pending`. Permit promotion. At `checks_pending`,
   expect `delivering` on `deliver`, an open submission and a `pending` turn. Press Enter after inspection, then press
   Enter at the release gate to delete the lever. At `checks_released`, expect `done`, a landing fact, a `flow-landed`
   event, and newest-first turns with `landed` ahead of at least one `pending`. Continue to teardown.

The printed IDs identify this run only. To run unattended, unset `BLIZZARD_ACTION_WALK`; the same fixture runs without
operator gates. Test failures retain hub/runner logs. The incompatible action fixture is confined to the disposable hub.
