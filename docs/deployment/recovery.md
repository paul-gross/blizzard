# Recovery

## Graceful restart

A graceful restart — `systemctl restart`, or stop-then-start on a wheel upgrade — lets the SIGTERM run the daemon's
shutdown path first: the shutdown marks every in-flight lease with a durable resume-intent, and the first tick RESUMEs
each marked session in place — same lease, epoch, and session, only the pid rewritten, no retry consumed — so in-flight
agent context is preserved, not merely "not worked twice". The graceful shutdown marks resume-intents without probing
health, since it knows the sessions were running a moment ago; the crash path must infer that after the fact.

A clean `systemctl stop` (or the stop half of a restart) still runs the shutdown pass and is exempt from `Restart=` —
only a failure or a boot brings a daemon back — so the machine can be taken down deliberately without a restart fight,
with in-flight leases still marked for restart-resume; the supervisor echoes "marked N in-flight lease(s) for
restart-resume" as it stops.

`runner pause` then `systemctl restart` is a plausible maintenance sequence, but the brake is a durable fact, not daemon
state: a runner paused before the restart stays paused after it, its marked sessions un-resumed until `runner start` is
run too — pause to stop new work landing mid-upgrade, then start again once the new wheel is confirmed healthy.

## Crashes and the startup pass

A crashed, OOM-killed, or kill-9ed daemon is brought straight back by the units' `Restart=always` (`RestartSec=2`), and
a reboot starts the enabled units at boot (`WantedBy=multi-user.target`); either way the startup pass recovers from the
durable on-disk store. An ungraceful `kill -9` skips the shutdown marking but not the resume: the next start marks the
crash-orphaned sessions before the loop begins (logging "marked N crash-interrupted lease(s) for restart-resume") and
the same RESUME re-attaches them — the cost is precision, not context.

The startup pass is the loop's normal first move, not special recovery code — provided the runner's own local brake is
off: with it on, REAP and RESUME still run but a stalled worker is not killed and a marked session is not re-attached;
both wait, exactly where the crash or shutdown left them, for the first tick after `runner start` clears the brake —
nothing lost, only deferred.

Both the shutdown and crash paths mark only live work with a session to re-attach: a lease still unspawned, dormant on a
question or an operator pause, or holding a buffered completion awaiting flush has nothing to resume — each is already
owned by the step that parked it. A standing operator `chunk pause` outranks restart-resume: a pause the runner already
parked on locally is never marked and stays parked, ADVANCE lifting it when the pause clears, while a pause recorded
only at the hub is discovered by RESUME's own re-attach read, which re-parks the lease instead of respawning — either
way the pause fact, not the restart, decides.

The crash path drops three more cases the shutdown path would have observed directly, and none lands in the same place:
a spawn that recorded a session-end goes exit-is-done, ADVANCE judging the completed work with no re-attach; a process
still alive goes to REAP, which decides on the heartbeat — still beating, it is re-adopted untouched and never
re-spawned, past the one-hour liveness window (which any outage over an hour guarantees, since heartbeats reach the
downed runner's own API) it is reaped and retried like any stalled worker; a heartbeat already stale at the crash means
the process is gone by construction, so REAP passes it and ADVANCE claims it — the verdict elicited from the dead
session, a retry consumed only when none can be, a failed attempt recorded via ADVANCE rather than a reap. The
session-end and stale-heartbeat cases converge on ADVANCE by different routes; ADVANCE consults no session-end fact —
the exit, not the declaration, routes a lease to it.

REAP expires narrowly: a lease minted but never spawned, and a worker still alive but stalled past the liveness window;
a session-bearing lease whose process is simply gone is not reaped — that one belongs to ADVANCE (the worker declared
done on the way out) or RESUME. What REAP expires becomes leasable again at its last-recorded node, never re-run from
the start, against environment bindings re-read from the store; facts are the only truth, so a restart reads exactly the
state a clean shutdown would have left. A crash during the re-attach itself degrades to the reap path: the resume is
bounded by the crash-point sweep's recovery, no stronger.

On the hub side, a completion re-flushed after a hub crash applies idempotently behind the epoch fence, and a per-repo
land already recorded is skipped on redelivery — a crash mid-delivery lands the chunk exactly once.

## How the contract is exercised

The recovery contract is exercised end-to-end by the whole-process cases of the kill-9 crash sweep — cases signalling a
whole daemon process rather than arming a registry crash point, plus one registry-armed case for a generic hub command
node's delivery: each runs the real build-then-deliver scenario with hub and runner as real subprocesses, restarts a
whole daemon from the same store directory (systemd's job, done by hand in the test), and asserts the chunk still
converges and lands exactly once, the facts-level invariant checker green after the crash and after recovery. In
[tests/crash/test_kill9_sweep.py](../../tests/crash/test_kill9_sweep.py):

- `test_graceful_restart_resumes_in_flight_session` gracefully restarts the supervisor while a worker is in flight; the
  shutdown marks the lease and the restart RESUMEs the same session in place, landing the chunk once without re-running
  from the top.
- `test_kill9_runner_daemon_after_session_end` kill-9s the supervisor strictly after the worker's commit is declared and
  its SessionEnd durably recorded; the restart reads that fact directly — no resume, no re-run — and the chunk
  converges.
- `test_kill9_at_hub_command_node_crash_point[hubnode.after-step.before-marker]` kill-9s the hub mid-delivery inside a
  generic hub command node's per-step window; the restart re-drives the executor off the re-flushed build completion and
  the change lands once.

Run just those three cases with `BLIZZARD_CRASH_SWEEP=1 uv run pytest` naming them; the run needs the sibling
blizzard-mock worktree and a local winter source, per the crash-sweep header.

The full sweep — the same recovery asserted at every boundary the crash-point registry enumerates
(`discover_crash_points`; `bzh:crash-point-registry`), including `kill -9` mid-RESUME at each graceful-restart boundary
(`test_kill9_at_resume_crash_point`) and mid-abandon at each detach boundary (`test_kill9_at_abandon_crash_point`) — is
`mise run crash-sweep`, and the tag release workflow runs it in CI.

[tests/test_systemd_units.py](../../tests/test_systemd_units.py) guards the unit files, holding their `ExecStart` to the
real shipped entry points and asserting the `Restart=` and boot-enable directives this contract depends on.
