# Recovery

## Graceful restart

A graceful restart — `systemctl restart`, or stop-then-start on a wheel upgrade — lets the SIGTERM run the daemon's
shutdown path first: the shutdown marks every in-flight lease with a durable resume-intent, and the first tick RESUMEs
each marked session in place — same lease, epoch, and session, only the pid rewritten, no retry consumed — so in-flight
agent context is preserved, not merely "not worked twice". The graceful shutdown marks resume-intents without probing
health, since it knows the sessions were running a moment ago; the crash path must infer that after the fact.

Right after marking, the shutdown drains its own workers: SIGINT each marked lease's process group, wait up to 60s total
(one shared budget across every marked worker, not 60s each), then SIGKILL whatever is still alive. Claude Code answers
SIGINT with an `error_during_execution` envelope that still carries a real `total_cost_usd` (but no `result` key), so
the next startup's restart-resume path records that generation's real spend rather than falling back to a NULL-cost
transcript sum. Two workers fall back the same way a crash-killed one always has: one that ignores SIGINT is SIGKILLed
at the deadline, and shutdown never hangs waiting on it; and one that exits on SIGINT without writing any envelope at
all, which Claude Code occasionally does — the drain counts it `exited_on_sigint`, yet its stdout file is empty. The
drain makes no durable write of its own: it only waits long enough for the envelope to reach the worker's stdout file,
which the restart's own usage recording already reads.

The unit declares `KillMode=mixed` and `TimeoutStopSec=120` so this stays the *only* thing that ever signals a worker:
under the default `control-group` mode systemd would SIGTERM every worker in the daemon's cgroup right alongside it,
racing the drain's own SIGINT and losing the real-cost envelope it exists to capture. `TimeoutStopSec=120` covers the
60s drain budget plus the rest of an orderly shutdown (the unbounded loop-thread join, the outbound buffer's last
flush); past it systemd SIGKILLs the whole cgroup — the same NULL-cost outcome a SIGKILLed worker already has.

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
the same RESUME re-attaches them — the cost is precision, not context. On OpenCode 2, whose turns run on the operator's
service rather than in the worker, the start first stops any turn a crash left running there (logging "stopped N turn(s)
a crash left running without a worker"), as [OpenCode configuration](./worker-spawn.md#opencode-configuration)
describes.

The startup pass is the loop's normal first move, not special recovery code — provided the runner's own local brake is
off: with it on, REAP and RESUME still run but a stalled worker is not killed and a marked session is not re-attached;
both wait, exactly where the crash or shutdown left them, for the first tick after `runner start` clears the brake —
nothing lost, only deferred.

Both the shutdown and crash paths mark only live work with a session to re-attach: a lease still unspawned, dormant on a
question or an operator pause, backing off after a provider overload, or holding a buffered completion awaiting flush
has nothing to resume — each is already owned by the step that parked it. A standing operator `chunk pause` outranks
restart-resume: a pause the runner already parked on locally is never marked and stays parked, ADVANCE lifting it when
the pause clears, while a pause recorded only at the hub is discovered by RESUME's own re-attach read, which re-parks
the lease instead of respawning — either way the pause fact, not the restart, decides.

The crash path drops three more cases the shutdown path would have observed directly, and none lands in the same place:
a spawn that recorded a session-end goes exit-is-done, ADVANCE judging the completed work — with no re-attach, unless a
required `produces:` is still unattached, which re-attaches the session in place instead, capped at one such resume per
`(lease, epoch)` before ADVANCE falls through to judging it anyway; a process still alive goes to REAP, which decides on
the heartbeat — still beating, it is re-adopted untouched and never re-spawned, past the one-hour liveness window (which
any outage over an hour guarantees, since heartbeats reach the downed runner's own API) it is reaped and retried like
any stalled worker; a heartbeat already stale at the crash means the process is gone by construction, so REAP passes it
and ADVANCE claims it — the verdict elicited from the dead session, a retry consumed only when none can be, a failed
attempt recorded via ADVANCE rather than a reap. The session-end and stale-heartbeat cases converge on ADVANCE by
different routes; ADVANCE consults no session-end fact — the exit, not the declaration, routes a lease to it.

A worker whose exit ADVANCE already claimed, but whose verdict elicitation is itself still in flight, survives a restart
the same way any other durable state does: the launch that recorded the in-flight row and the pid it started are both
facts on disk, not daemon memory. REAP still passes the lease (the worker's own pid is long gone, exit-is-done), and
ADVANCE's collect check reads the elicitation's own pid against `/proc` fresh — a process that outlived the crash is
re-adopted untouched, its reply collected whenever it finishes; one that did not is read as lost, exactly like an
ordinary crash mid-elicitation, and relaunched under the same staleness bound a live loss uses (no retry consumed either
way), or the attempt is failed once that bound has passed. A crash between the in-flight record landing and the process
actually starting — the same un-armable gap `SPAWN`'s own mint-before-spawn window accepts — is read the same way: no
recorded pid reads as "not running," so recovery relaunches rather than waiting on a process nothing can confirm exists.

REAP expires narrowly: a lease minted but never spawned, and a worker still alive but stalled past the liveness window;
a session-bearing lease whose process is simply gone is not reaped — that one belongs to ADVANCE (the worker declared
done on the way out) or RESUME. What REAP expires becomes leasable again at its last-recorded node, never re-run from
the start, against environment bindings re-read from the store; facts are the only truth, so a restart reads exactly the
state a clean shutdown would have left. A crash during the re-attach itself degrades to the reap path: the resume is
bounded by the crash-point sweep's recovery, no stronger.

## Execution evidence and the no-progress warning

The heartbeat is not the only thing that keeps a live worker inside the liveness window. About once a minute the runner
asks each live lease's harness what it can see of that lease's current execution, and records the answer in the
`execution_observations` table of `host.db`. When the harness reports the execution running, with evidence produced
since the current spawn, that evidence renews the window exactly as a heartbeat does, so a single tool call running
longer than an hour is not reaped while the harness keeps reporting it. Evidence from an earlier spawn of the lease, or
an answer the harness cannot tie to the lease's own session, renews nothing; once the evidence stops, the window runs
out from the last evidence, as it does from the last heartbeat. A harness that cannot observe its executions answers
`unknown` and changes nothing: its leases are reaped on heartbeats alone, as before.

Liveness is not progress. When a worker the harness reports running has made no progress — no completed tool call, no
progress the harness reports — for 15 minutes, the runner raises one worker-no-progress warning in the operational event
feed ([observability.md](./observability.md)), naming the worker's current activity, how old its evidence is and how
long it has gone without progress, and logs it at WARNING. It fires once per stall; observations that only show the
worker still running neither repeat it nor clear it, and progress resuming re-arms it. The warning stops nothing — the
harness's own tool timeouts still bound every call.

On the hub side, a completion re-flushed after a hub crash applies idempotently behind the epoch fence, and a per-repo
land already recorded is skipped on redelivery — a crash mid-delivery lands the chunk exactly once.

## Provider-overload backoff

A worker generation or judge elicitation can exit because the harness's own provider returned an overload signal (Claude
Code's `server_error`/529 assistant reply, an OpenCode 529 or `overloaded` error event) rather than because the turn
actually finished. The runner classifies that exit the same way it classifies a usage limit — a translated fact, never
inferred from cost or token figures — and, short of five consecutive overloads on the one lease, backs the lease off
instead of judging it: no verdict is elicited, no retry is consumed, and the epoch is unchanged. The agent slot and
every bound environment stay held exactly as a dormant, ask-parked lease's do, and the same session resumes in place —
only `pid`/`process_start_time` rewritten — once a durable `resume_after` passes: 60s after the first overload in a
streak, doubling each further consecutive one (120s, 240s, 480s), capped at 15 minutes though the cap is never actually
reached before the streak limit. A clean exit closes an open streak, so a later, unrelated overload starts fresh at the
first delay rather than continuing where an old streak left off.

`resume_after` is a durable fact, not daemon or in-memory state, so a backing-off lease survives a restart or a crash
exactly like an ask- or pause-parked one: REAP and RESUME leave it alone, and ADVANCE's own no-op-until-due check
(`bzh:facts-not-status` — "is it backing off" is never itself cached; each tick derives it fresh off the durable
`resume_after`) picks the wait back up wherever the outage left it, waking it late rather than early or not at all. A
judge elicitation's own overload is closed the same way a worker generation's is — by the next invocation's own identity
moving on, generation for a worker, launch instant for a judge — never a separate closing write. The fifth consecutive
overload on a lease falls through to today's ordinary path: a worker generation is judged as usual, a verdict-less judge
elicitation fails the attempt as usual, spending a retry only there.

## Recovering a forge-auth escalation

A chunk escalated `forge-auth` stopped at its `deliver` step because GitHub refused the credential a call carried
([credentials.md](./credentials.md#what-is-refused)). Nothing it did is undone: an open change stays open, its branch
stays on the forge, and every repository that already landed stays landed. Recover it in three steps:

1. **Read the detail.** `blizzard hub chunk show <chunk>` shows the escalation's detail: the repository and the refused
   call, the refusal, GitHub's own words, the credential, and the remedy for that refusal, such as granting a permission
   to the App installation, replacing the token, or authorizing it for the organization's SSO. Its last line is the two
   commands below, with the credential, repository and chunk filled in. A call that carried only a secret has no such
   line.
2. **Fix the credential, then check it.** Apply the remedy, then run
   `blizzard hub credential check <credential> --repo <repository>`. It reads the forge as the credential and sends no
   write ([credentials.md](./credentials.md#checking-a-credential)); it exits 0 once it reaches the repository and
   nothing is `refused` or `unsupported`. A check that reached no use exits 1 and says why, such as an installation the
   forge did not answer for: check again before requeuing.
3. **Requeue.** `blizzard hub chunk requeue <chunk>` sends the chunk back to its `deliver` step. The `land` action reads
   the forge before it acts: it reuses the open change rather than opening another, and skips every repository already
   landed, so each repository's landing is recorded once.

A check that passes does not prove a write will succeed, and does not read branch protection. If the requeued step is
refused again, its new escalation names what is still missing.

## How the contract is exercised

The recovery contract is exercised end-to-end by the whole-process cases of the kill-9 crash sweep — cases signalling a
whole daemon process rather than arming a registry crash point, plus one registry-armed case for a hub node's `land`
pass: each runs the real build-then-deliver scenario with hub and runner as real subprocesses, restarts a whole daemon
from the same store directory (systemd's job, done by hand in the test), and asserts the chunk still converges and lands
exactly once, the facts-level invariant checker green after the crash and after recovery. In
[tests/crash/test_kill9_sweep.py](../../tests/crash/test_kill9_sweep.py):

- `test_graceful_restart_resumes_in_flight_session` gracefully restarts the supervisor while a worker is in flight; the
  shutdown marks the lease and the restart RESUMEs the same session in place, landing the chunk once without re-running
  from the top.
- `test_kill9_runner_daemon_after_session_end` kill-9s the supervisor strictly after the worker's commit is declared and
  its SessionEnd durably recorded; the restart reads that fact directly — no resume, no re-run — and the chunk
  converges.
- `test_kill9_between_land_repositories` kill-9s the hub between a two-repository `land`'s repositories, the first
  repository's landing record durable and the second not yet merged; the restart lands the second without merging the
  first twice.

Run just those three cases with `BLIZZARD_CRASH_SWEEP=1 uv run pytest` naming them; the run needs the sibling
blizzard-mock worktree and a local winter source, per the crash-sweep header.

The full sweep — the same recovery asserted at every boundary the crash-point registry enumerates (the test suite's own
`discover_crash_points`; `bzh:crash-point-registry`), including `kill -9` mid-RESUME at each graceful-restart boundary
(`test_kill9_at_resume_crash_point`), mid-abandon at each detach boundary (`test_kill9_at_abandon_crash_point`), and at
the hub's own pass boundaries: the effect writer's `action.after-effect.before-write` and
`action.after-write.before-resume`, and the flow driver's turns — `flow.after-submit.before-turn` between a submission
and its turn, `flow.after-turn-start.before-pass` between a turn's start and its pass, `flow.after-poll.before-turn-end`
inside the transaction holding a pending pass's poll row and its turn's end, and `flow.boot.after-interrupt` between a
tenant's interrupted turns and its line reconciliation at boot — is `mise run crash-sweep`. The tag release workflow
runs the full sweep in CI for candidates. Any stable tag, including a patch, can borrow a green candidate release run
for the same version and commit and skip the sweep. A promotion without that proof is refused; a patch without it runs
the full sweep.

[tests/guards/test_systemd_units.py](../../tests/guards/test_systemd_units.py) guards the unit files, holding their
`ExecStart` to the real shipped entry points and asserting the `Restart=` and boot-enable directives this contract
depends on.
