# Controlled clock: live hub acceptance

## Repeat after fixes — 2026-11-04

**Result: pass** for the reviewed plan's live HTTP checks on `r-chatgpt-1/blizzard`, base `62be457dd` with the current
uncommitted fixes. The earlier failed run is retained below. The repeat used existing provisioning;
`winter service up r-chatgpt-1 --wait` exited 0. The env hub's health and readiness returned 200 before and after the
exercise, with readiness true at revision `20261101_0900_turn_page`.

The fresh `repeat-runtime` SQLite store was initialized with `uv run blizzard hub init`, `BZ_HUB_DB_URL` unset, and the
same auth-none, forge-writes-disabled, six-stepped-sweep configuration as the earlier run. The scratch hub listened at
`127.0.0.1:4778`; health and readiness returned 200 in every mode. Commands, responses, scripts, server logs and PID
records are in `$BLIZZARD_TMPDIR/hub-controlled-clock/`: `repeat.py`, `repeat.log`, `repeat-service-up.log`,
`repeat-{controlled,system,legacy}.log` and `repeat-{controlled,system,legacy}-pids.txt`. The repeat script invokes the
retained `exercise.py` and `invalid.py`.

- Controlled mode (`BLIZZARD_CLOCK=controlled`, `BLIZZARD_CLOCK_FENCE=1`): two reads and the `hub-started` event agreed
  at `2026-11-04T07:59:06.676158+00:00`. Consecutive steps reconciled 1 then skipped 1. Advancing 90.5 seconds reached
  `08:00:37.176158+00:00`; clock and event list remained unchanged over 5.011 seconds, and the next step reconciled 1 at
  that time. The legacy advance route returned 404.
- A zoned `to` moved to `08:01:37.176158+00:00`; equal `to` and zero advance returned 200, and independent reads agreed.
  Backwards movement returned 409 `clock_backwards`. Empty, ambiguous, negative, unzoned, `NaN`, `Infinity`, and
  `-Infinity` inputs returned 422, with the clock unchanged after each invalid input.
- System restart, with clock and legacy variables unset: clock GET and POST and legacy step and advance POST each
  returned 404. Legacy restart, with only `BLIZZARD_TEST_FLOW_STEP=1` and `BLIZZARD_CRASH_FENCE=1`: clock GET and POST
  returned 404; step returned 200 and reconciled 1 at `07:59:17.008613+00:00`, advance returned 200 at
  `08:01:17.008613+00:00`, and another step reconciled 1 at that advanced instant.
- Scratch process groups/PIDs 1418748, 1419110 and 1419171 were sent SIGTERM and waited for, each exiting 0. No child
  PID was reported by `/proc` at capture; the recorded PIDs were absent afterward and port 4778 had no listener.
  Authorization and held-step races remain delegated to component verification; this auth-none live run does not claim
  them.

## Earlier run before fixes — failed

2026-11-04, `r-chatgpt-1/blizzard`, base `62be457dd` with the controlled-clock worktree changes. Result: **failed** the
reviewed plan's exact HTTP expectations below. Full scripts, bodies, process logs, and PIDs were captured under
`$BLIZZARD_TMPDIR/hub-controlled-clock/` for this lease.

## Setup

- `winter provision r-chatgpt-1` and `winter service up r-chatgpt-1 --wait` exited 0. After
  `source <(winter env r-chatgpt-1)`, `GET http://127.0.0.1:4762/api/health` returned 200 `status: ok` and `/api/ready`
  returned 200 `ready: true`, revision `20261101_0900_turn_page`.
- `env -u BZ_HUB_DB_URL uv run blizzard hub init "$BLIZZARD_TMPDIR/hub-controlled-clock/runtime"` initialized a SQLite
  store and default tenant. The scratch config disabled forge writes, used `auth.mode = "none"`, and set all six sweeps
  (`forge_status`, `event_derivation`, `close_drain`, `tenant_teardown`, `trace_export`, `egress`) to `stepped`.
- Scratch base `http://127.0.0.1:4778` was unused and within the 4760–4779 environment band. Hosted with
  `BLIZZARD_CLOCK=controlled BLIZZARD_CLOCK_FENCE=1 uv run blizzard hub host --dir <runtime> --host 127.0.0.1 --port 4778`,
  clearing `BZ_HUB_DB_URL` and both legacy variables. Health and readiness both returned 200.

## Controlled mode

- Two `GET /api/admin/clock` calls returned `2026-11-04T07:50:19.454567+00:00`, identical to the `hub-started` event's
  `recorded_at` in `GET /api/admin/events?limit=100`.
- Two `POST /api/admin/test-flow/step` calls returned 200, respectively `reconciled: 1, skipped: 0` and
  `reconciled: 0, skipped: 1`. `POST /api/admin/clock` with `{"advance_seconds":90.5}` returned previous
  `07:50:19.454567+00:00` and now `07:51:49.954567+00:00`; an independent GET agreed. After 5.004 seconds, the event
  list and clock were unchanged. The next step returned `reconciled: 1` at the moved reading; no automatic
  reconciliation consumed that due work.
- **Failure:** `POST /api/admin/test-flow/advance` returned 405 `{"detail":"Method Not Allowed"}`, not the planned 404.
- A zoned `to` moved to `2026-11-04T07:52:49.954567+00:00`; equal `to` and zero advance returned 200. Independent GETs
  agreed. Setting the earlier boot instant returned 409 `{"code":"clock_backwards"}`. Empty, ambiguous,
  negative-duration, and unzoned bodies returned 422; subsequent GETs preserved the later time.
- **Failure:** `{"advance_seconds":NaN}`, `Infinity`, and `-Infinity` each returned 500 `Internal Server Error`, not
  422; the clock remained unchanged. The server log shows Pydantic's `finite_number` validation error followed by
  `ValueError: Out of range float values are not JSON compliant` while serializing the error response.

## Restart modes and cleanup

The controlled scratch child PID 1376105 and uv PID 1376079 were recorded; the child was sent SIGTERM and both exited.
The same runtime was restarted via `uv run blizzard hub host` with all clock and legacy variables unset, then with only
`BLIZZARD_TEST_FLOW_STEP=1 BLIZZARD_CRASH_FENCE=1`. Both restarts were healthy and ready. Recorded uv PIDs 1389380 and
1389521 were sent SIGTERM and waited for; each exited 0. An initial system restart (uv 1385130, server 1385133) also
shut down cleanly when PID discovery failed; the system check was then repeated. Port 4778 had no listener afterward.

- **Failure:** in both restart modes, `GET /api/admin/clock` returned 200 with the built board's HTML fallback and
  `POST /api/admin/clock` returned 405, rather than the planned 404. In system mode the legacy step and advance POSTs
  also returned 405 rather than 404. These responses are not successful clock-control responses.
- Legacy mode: step returned 200 with `reconciled: 1` at `2026-11-04T07:53:00.337013+00:00`; advance with
  `{"seconds":120}` returned 200 and `2026-11-04T07:55:00.337013+00:00`; a second step reconciled at that reading.
- Final `GET http://127.0.0.1:4762/api/health` and `/api/ready` both returned 200; readiness remained true at the same
  store revision. Authorization and held-step races are delegated by the plan to component verification, not claimed by
  this auth-none live exercise.
