# Crash and reboot recovery

## The recovery contract

Two systemd mechanisms combine to deliver the journey's "came back under systemd":

| Failure                                                                  | What systemd does                                                                                       | What blizzard does on restart                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `kill -9`, OOM, or crash of a daemon                                     | `Restart=always` brings it straight back (`RestartSec=2`)                                               | Startup pass recovers from the durable store — see below                                                                                                                                                                                                                                                                                           |
| Machine reboot                                                           | The enabled units start at boot (`WantedBy=multi-user.target`)                                          | Same startup pass, from the same on-disk store                                                                                                                                                                                                                                                                                                     |
| Graceful restart (`systemctl restart`, or stop→start on a wheel upgrade) | The SIGTERM lets the daemon run its shutdown path *before* exiting; `Restart=`/boot then brings it back | The shutdown marks every in-flight lease with a durable resume-intent; the first tick **RESUMEs** each session in place — same lease/epoch/session, only the pid rewritten, no retry consumed — so **in-flight agent context is preserved**, not merely "not worked twice" (unless the lease is under a standing operator chunk pause — see below) |

The startup pass is where the "reaped the stale leases … continued at exactly the node the hub last recorded" clause is
honored, and it is **not** new code — it is the loop's normal first move — **provided the runner's own brake
(`runner pause`, issue #45) is off.** If it is on, the runner's first tick(s) after a restart still run REAP and RESUME,
but a stalled worker is not killed and a marked session is not re-attached — both wait, exactly where the crash or the
shutdown left them, for the first tick after `runner start` clears the brake. Nothing described below is lost in the
meantime, only deferred.

- **Supervisor.** The runner's first tick after any restart is **REAP**, and it expires narrowly: a lease minted but
  never spawned, and a worker still alive but stalled past the liveness window. A session-bearing lease whose process is
  simply gone is *not* reaped — that one is either ADVANCE's (the worker declared done on the way out) or RESUME's (the
  paragraph below owns it). What REAP does expire becomes leasable again at its last-recorded node, never re-run from
  the start, against environment bindings re-read from the store. Facts are the only truth, so a restart reads exactly
  the state a clean shutdown would have left.
- **Hub.** A completion re-flushed after a hub crash is applied idempotently behind the epoch fence, and a per-repo land
  already recorded is skipped on redelivery — so a crash mid-delivery lands the chunk exactly once, not twice.

A **graceful** restart does one better than reaping. Because the SIGTERM lets the supervisor run a shutdown pass before
it exits, it marks the in-flight leases with a durable *resume-intent* — without probing their health, since it knows
they were running a moment ago, where the crash path has to infer that after the fact. The first tick after the restart
then **RESUMEs** each marked session in place — the same lease, epoch, and session, only the process id rewritten and no
retry consumed — so a `systemctl restart` (for example, to adopt a freshly-merged runner wheel) continues each agent
mid-thought rather than reaping and re-running it from the top — **provided the chunk isn't under a standing operator
pause** (issue #46; see [Chunk and runner control verbs](./control-verbs.md)). A pause the runner has already parked on
locally is not marked at all and simply stays parked, ADVANCE lifting it when the pause clears; a pause recorded only at
the hub is discovered by RESUME's own re-attach read, which re-parks the lease instead of respawning it. Either way the
pause fact, not the restart, decides. An ungraceful `kill -9` skips the *shutdown* marking, but not the resume: the next
start marks the sessions the crash orphaned before the loop begins
(`marked N crash-interrupted lease(s) for restart-resume`), so the same RESUME re-attaches them. What it costs is
precision, not the context.

Both paths mark only a lease that is **live work with a session to re-attach to** — never one still unspawned, dormant
on a question or an operator pause, or holding a buffered completion awaiting flush. Those three are not exclusions from
resume so much as leases with nothing to resume: each is already owned by the step that parked it. On top of that floor
the crash path drops three more, inferring after the fact what the shutdown path observed directly — and none of the
three lands in the same place:

| Excluded because                             | What happens instead                                                                                                                                                                                                                                                                                             |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| the spawn recorded a session-end             | exit-is-done: ADVANCE judges the completed work, no re-attach needed                                                                                                                                                                                                                                             |
| the process is still alive                   | REAP decides on the heartbeat, not the crash: still beating, it is re-adopted untouched and never re-spawned; already past the one-hour liveness window — which any outage longer than an hour guarantees, since heartbeats reach the downed runner's own API — it is reaped and retried like any stalled worker |
| the heartbeat was already stale at the crash | its process is gone by construction (that is the test above it), so REAP passes it over and **ADVANCE** claims it: the verdict is elicited from the dead session, and a retry is consumed only if none can be — a failed attempt recorded via ADVANCE, not a reap                                                |

Rows one and three therefore converge on ADVANCE, reached by different routes: the first declared itself done, the third
merely exited. ADVANCE consults no session-end fact — the exit, not the declaration, is what routes a lease to it.

And a crash *during* the re-attach itself degrades to the reap path — the resume is bounded by the crash-point sweep's
recovery, no stronger.

`runner pause`, then `systemctl restart` to adopt a new wheel, is a plausible maintenance sequence — but a runner paused
*before* the restart stays paused after it (the brake is a durable fact, not daemon state), so its marked sessions sit
un-resumed until `runner start` is run too. Pause to stop new work landing mid-upgrade, then start again once the new
wheel is confirmed healthy, the same way you would leave it paused across any other maintenance window.

A clean `systemctl stop` (or the stop half of a restart) still runs that shutdown pass: it is exempt from `Restart=` —
only a failure or a boot brings a daemon back — so an operator can take the machine down deliberately without a restart
fight, **and** any in-flight leases are marked for restart-resume, so a later start re-attaches them rather than
re-running them. The supervisor echoes `marked N in-flight lease(s) for
restart-resume` as it stops.

## The recovery demo — run it and watch it hold

The behavior above is exercised end-to-end by **whole-process** cases of the kill-9 crash sweep — cases that signal a
whole daemon process rather than arming a registry crash point — plus a registry-armed case for a generic hub command
node's delivery. They *are* the recovery demo: each runs the real `build → deliver` scenario with the hub and runner as
real subprocesses, then restarts a whole daemon from the same store directory (systemd's job, done by hand in the test)
and asserts the chunk still converges and lands **exactly once**, with the facts-level invariant checker green after the
crash and again after recovery:

- `tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_after_session_end` — `kill -9`s the **supervisor** strictly
  after the in-flight worker's commit is declared and its `SessionEnd` is durably recorded; the restart reads that fact
  directly (no resume, no re-run) and the chunk converges.
- `tests/crash/test_kill9_sweep.py::test_kill9_at_hub_command_node_crash_point[hubnode.after-step.before-marker]` —
  `kill -9`s the **hub** mid-delivery, inside a generic hub command node's per-step window; the restart re-drives the
  executor off the re-flushed build completion and the change lands once.
- `tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session` — **gracefully** restarts the
  supervisor while a worker is in flight; the shutdown marks the lease and the restart RESUMEs the *same* session in
  place, so the chunk lands once without re-running from the top.

Run just the demo (needs the sibling `blizzard-mock` worktree and a local winter source — see the crash-sweep header):

```bash
BLIZZARD_CRASH_SWEEP=1 uv run pytest \
  tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_after_session_end \
  "tests/crash/test_kill9_sweep.py::test_kill9_at_hub_command_node_crash_point[hubnode.after-step.before-marker]" \
  tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session
```

The full sweep — the same recovery asserted at every boundary the crash-point registry enumerates
(`discover_crash_points`; see `bzh:crash-point-registry`), including a `kill -9` *mid-RESUME* at each graceful-restart
boundary (`test_kill9_at_resume_crash_point`) and mid-abandon at each detach boundary
(`test_kill9_at_abandon_crash_point`) — is `mise run crash-sweep`, and the tag `release` workflow runs it in CI. The
unit files themselves are guarded by `tests/test_systemd_units.py`, which holds their `ExecStart` to the real shipped
entry points and asserts the `Restart=` and boot-enable directives this contract depends on.
