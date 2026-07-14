# Deployment and boot recovery

How a colocated blizzard machine — one hub and one supervisor (runner) side by
side — is installed under systemd, and the contract that makes it survive a crash
or a reboot with nothing lost and nothing worked twice. This is the operator
reference for the journey clause in the product spec
(`blizzard-discovery` `product/mvp.md`):

> At some point in the night the machine rebooted. It didn't matter: the
> supervisor and the colocated hub came back under systemd, the supervisor reaped
> the stale leases, re-read the environment bindings from its store, and continued
> — every chunk still at exactly the node the hub last recorded.

The two units live in [`packaging/systemd/`](../packaging/systemd/):
[`blizzard-hub.service`](../packaging/systemd/blizzard-hub.service) and
[`blizzard-runner.service`](../packaging/systemd/blizzard-runner.service).

## The colocated topology

One machine runs both daemons of a single-runner deployment (the MVP shape — a
remote hub and multiple runner machines are on the cut list). They are two
personalities of the one `blizzard` wheel (D-061), so there is no version skew
between them and no Node at install or runtime:

- **hub** — `blizzard-hub host`: the fleet's HTTP API, SSE, and the embedded
  mission-control board. Holds the forge base URL and PM credentials (D-047/D-084)
  — those live only here, never on the runner.
- **supervisor (runner)** — `blizzard-runner host`: the stateless
  `REAP → PULL → FILL → ADVANCE` loop behind a machine-local API. Reaches the hub
  outbound-only (D-012), so it keeps working while the hub is briefly unreachable.

Each daemon owns its own embedded store; neither opens the other's.

## Install

Install the wheel into a self-contained, node-free virtualenv, seed each daemon's
runtime directory once, drop the units, and enable them:

```bash
# 1. Install the one wheel into a dedicated venv (the path the units' ExecStart use).
python3 -m venv /opt/blizzard/venv
/opt/blizzard/venv/bin/pip install blizzard-<version>-py3-none-any.whl

# 2. A service account and the shared state root the units declare (StateDirectory).
useradd --system --home-dir /var/lib/blizzard --shell /usr/sbin/nologin blizzard

# 3. Seed each runtime dir: config scaffold + data dir + a store migrated to head
#    (D-099). Idempotent — safe to re-run.
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-hub    init /var/lib/blizzard/hub
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner init /var/lib/blizzard/runner

# 4. Point the hub at the forge and the runner at its workspace. The hub's
#    credentials go in /etc/blizzard/hub.env (BZ_FORGE_URL, BZ_FORGE_TOKEN, …);
#    the runner's workspace/harness bindings live in its own config.toml, written
#    by `init` and edited in place (no credentials — D-084).

# 5. Install and enable both units. `enable` is what starts them at boot; `--now`
#    starts them immediately too.
sudo cp packaging/systemd/blizzard-hub.service packaging/systemd/blizzard-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now blizzard-hub.service blizzard-runner.service
```

If the wheel is installed somewhere other than `/opt/blizzard/venv`, edit the
`ExecStart`/`ExecStartPre` paths to match `command -v blizzard-hub` — systemd
requires an absolute path there.

**Upgrades self-heal the store.** To adopt a new wheel, `pip install` it into the
venv and `systemctl restart` the units — no manual migration step. Each unit's
`ExecStartPre` runs `… migrate` before the daemon opens its store, so a wheel that
ships a new schema revision (D-099) reconciles the on-disk store to head on the next
start; the daemon refuses to start on a revision mismatch, so a forgotten migration
fails loudly rather than corrupting state. A graceful `systemctl restart` also
preserves in-flight work across the upgrade — see the recovery contract below.

## The recovery contract

Two systemd mechanisms combine to deliver the journey's "came back under systemd":

| Failure | What systemd does | What blizzard does on restart |
|---------|-------------------|-------------------------------|
| `kill -9`, OOM, or crash of a daemon | `Restart=always` brings it straight back (`RestartSec=2`) | Startup pass recovers from the durable store — see below |
| Machine reboot | The enabled units start at boot (`WantedBy=multi-user.target`) | Same startup pass, from the same on-disk store |
| Graceful restart (`systemctl restart`, or stop→start on a wheel upgrade) | The SIGTERM lets the daemon run its shutdown path *before* exiting; `Restart=`/boot then brings it back | The shutdown marks every in-flight lease with a durable resume-intent (D-082); the first tick **RESUMEs** each session in place — same lease/epoch/session, only the pid rewritten, no retry consumed — so **in-flight agent context is preserved**, not merely "not worked twice" |

The startup pass is where the "reaped the stale leases … continued at exactly the
node the hub last recorded" clause is honored, and it is **not** new code — it is
the loop's normal first move:

- **Supervisor.** The runner's first tick after any restart is **REAP**. It reaps
  the leases the crash stranded (their workers are gone), re-reads its environment
  bindings from its store, and each chunk becomes leasable again at its
  last-recorded node — never re-run from the start. Facts are the only truth
  (D-004), so a restart reads exactly the state a clean shutdown would have left.
- **Hub.** A completion re-flushed after a hub crash is applied idempotently
  behind the epoch fence (D-042), and a per-repo land already recorded is skipped
  on redelivery — so a crash mid-delivery lands the chunk exactly once, not twice.

A **graceful** restart does one better than reaping. Because the SIGTERM lets the
supervisor run a shutdown pass before it exits, it marks every in-flight lease with
a durable *resume-intent* (D-082) instead of leaving its workers to be reaped. The
first tick after the restart then **RESUMEs** each marked session in place — the same
lease, epoch, and session, only the process id rewritten and no retry consumed — so a
`systemctl restart` (for example, to adopt a freshly-merged runner wheel) continues
each agent mid-thought rather than reaping and re-running it from the top. An
ungraceful `kill -9` skips the marking, so its workers fall back to the reap path
above; and a crash *during* the re-attach itself degrades to that same reap path — the
resume is bounded by the crash-point sweep's recovery, no stronger.

A clean `systemctl stop` (or the stop half of a restart) still runs that shutdown pass:
it is exempt from `Restart=` — only a failure or a boot brings a daemon back — so an
operator can take the machine down deliberately without a restart fight, **and** any
in-flight leases are marked for restart-resume, so a later start re-attaches them
rather than re-running them. The supervisor echoes `marked N in-flight lease(s) for
restart-resume` as it stops.

## The recovery demo — run it and watch it hold

The behavior above is exercised end-to-end by the three **whole-process** cases of
the kill-9 crash sweep. They *are* the recovery demo: each runs the real
`build → deliver` scenario with the hub and runner as real subprocesses, then
restarts a whole daemon from the same store directory (systemd's job, done by hand
in the test) and asserts the chunk still converges and lands **exactly once**, with
the facts-level invariant checker green after the crash and again after recovery:

- `tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_mid_flight` — `kill -9`s
  the **supervisor** mid-flight; the restart's REAP reaps the stranded lease and
  the chunk converges.
- `tests/crash/test_kill9_sweep.py::test_kill9_hub_mid_delivery` — `kill -9`s the
  **hub** mid-delivery; the restart re-applies the completion idempotently and the
  change lands once.
- `tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session`
  — **gracefully** restarts the supervisor while a worker is in flight; the shutdown
  marks the lease and the restart RESUMEs the *same* session in place (D-082), so the
  chunk lands once without re-running from the top.

Run just the demo (needs the sibling `blizzard-mock` worktree and a local winter
source — see the crash-sweep header):

```bash
BLIZZARD_CRASH_SWEEP=1 uv run pytest \
  tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_mid_flight \
  tests/crash/test_kill9_sweep.py::test_kill9_hub_mid_delivery \
  tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session
```

The full sweep — the same recovery asserted at all 25 crash-point-registry
boundaries, including a `kill -9` *mid-RESUME* at each graceful-restart boundary
(`test_kill9_at_resume_crash_point`) — is `mise run crash-sweep`, and the tag
`release` workflow runs it in CI. The unit files themselves are guarded by
`tests/test_systemd_units.py`, which holds their `ExecStart` to the real shipped
entry points and asserts the `Restart=` and boot-enable directives this contract
depends on.
