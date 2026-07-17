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
#    delivery credentials go in /etc/blizzard/hub.env (BZ_FORGE_URL, BZ_FORGE_TOKEN, …);
#    its PM work sources are declared in blizzard-hub.toml's [[pm_source]] blocks
#    (init scaffolds a commented-out example — see "Configuring PM work sources"
#    below); the runner's workspace/harness bindings live in its own config.toml,
#    written by `init` and edited in place (no credentials — D-084).

# 5. Install and enable both units. `enable` is what starts them at boot; `--now`
#    starts them immediately too.
sudo cp packaging/systemd/blizzard-hub.service packaging/systemd/blizzard-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now blizzard-hub.service blizzard-runner.service
```

If the wheel is installed somewhere other than `/opt/blizzard/venv`, edit the
`ExecStart`/`ExecStartPre` paths to match `command -v blizzard-hub` — systemd
requires an absolute path there.

**Upgrades self-heal the store — for an additive or backfill revision.** To adopt a new
wheel, `pip install` it into the venv and `systemctl restart` the units — no manual
migration step. Each unit's `ExecStartPre` runs `… migrate` before the daemon opens its
store, so a wheel that ships a new schema revision (D-099) reconciles the on-disk store
to head on the next start; the daemon refuses to start on a revision mismatch, so a
forgotten migration fails loudly rather than corrupting state. A graceful `systemctl
restart` also preserves in-flight work across the upgrade — see the recovery contract
below. That loud-failure guarantee is the whole safety story for a revision whose
`upgrade()` only adds or backfills; it is not for a **destructive** one, whose
`upgrade()` deletes rows outright — see "The 0014 upgrade note" below for the one
revision so far that does.

### The 0014 upgrade note

**`0014_hub_pr_opened_idempotent` is the first migration in either store whose
`upgrade()` deletes rows** (`0003`/`0005` are the only other destructive revisions in
either tree, and both only drop columns). Closing a coordinator read-then-write race
(issue #10) with a unique constraint on `(chunk_id, repo)` first requires a store
carrying the race's duplicate rows to no longer carry them, so `upgrade()` deletes every
`delivery_pr_opened` row but the earliest per `(chunk_id, repo)` before adding the
constraint. `downgrade()` only drops the constraint back — it does not restore the
deleted rows; they are gone for good.

In practice this only ever removes true duplicates (a redundant `pr.opened` fact for a
PR the forge had already deduplicated to one), so no chunk loses a fact a human or the
board ever relied on distinguishing. But because the delete is unconditional and
irreversible, **copy the hub's store file before restarting into a wheel carrying this
migration** — `cp <hub-dir>/data/hub.db <hub-dir>/data/hub.db.pre-0014` for the sqlite
default, or the equivalent for a configured postgres `db_url` (`bzh:sql-portable`) —
the same caution any one-way migration deserves, and not something `migrate`'s
revision-mismatch guard can catch after the fact, since the delete is exactly what
reaching that revision means.

## Naming the runtime directory

Every verb that takes a runtime dir — `init`'s positional `DIRECTORY`, and `--dir` on
`migrate`, `host`, `runner tick`, `runner pause`, and `runner start` — resolves it from
three rungs, highest to lowest: the explicit flag or argument, then an environment
variable, then the current working directory.

| Daemon | Variable | Names |
|--------|----------|-------|
| hub | `BZ_HUB_DIR` | the hub runtime dir (`blizzard-hub.toml` + `data/hub.db`) |
| runner | `BZ_RUNNER_DIR` | the runner runtime dir (`blizzard-runner.toml` + `data/runner.db` + `runner.sock`) |

The units above pass `--dir` explicitly, so they are unaffected. The variable is for
callers that cannot hand-write a flag at every invocation — an operator shell aimed at a
deployment, or winter's per-env band pointing one feature env at a store snapshot or at a
shared runtime dir during an exclusive handoff.

> **Selectable is not shareable.** The store is single-writer, and each daemon migrates
> on boot. Aiming a second live daemon at a runtime dir a running instance already holds
> risks lock contention and corruption — this variable chooses a root, it does not make
> one safe to share.

## Configuring PM work sources

The hub's PM pass-through (D-047, MVP criterion 1) reads every chunk's PM item through a
**configured PM work source** — a named, credentialed binding to one forge repo, declared
as an `[[pm_source]]` table in `blizzard-hub.toml`. This is a separate seam from the
delivery forge above: `BZ_FORGE_URL`/`BZ_FORGE_TOKEN` in the hub's env file control where
a chunk's PR is opened and landed; `[[pm_source]]` controls where its PM item is *read
from*, and each source carries its own credential (D-108) rather than sharing the
delivery forge's.

`blizzard hub init` scaffolds a commented-out example block — uncomment it and fill in
your own repo to configure a source:

```toml
[[pm_source]]
name = "blizzard"                                  # source id — ingest tokens and board labels key on it
provider = "github"                                # the only adapter grammar today
repo = "paul-gross/blizzard"                       # the "owner/repo" this source is pinned to
token_env = "BZ_PM_TOKEN"                          # names an env var — see credentials below
# api_base = "https://ghe.example.internal/api/v3" # optional: override the provider's API origin
# web_base = "https://ghe.example.internal"         # optional: override the web origin
```

Every field:

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | The source's identity. Ingest tokens (`name:ref`, `name#ref`) and board pointer labels (`{source}#{ref}`) key on it. Must not contain `:` (the ingest token grammar splits on the first one). Must be unique across all `[[pm_source]]` blocks. |
| `provider` | yes | The adapter grammar this source speaks. Only `"github"` exists today; an unknown provider fails at config load, not at first use. |
| `repo` | yes | The `owner/name` coordinate this source is pinned to. Each `(provider, repo)` pair may appear under only one `name` — two names for the same repo would let one item be ingested twice under two identities (D-093). |
| `token_env` | yes | Names an environment variable — **not the secret itself** (D-084). See "Credential indirection" below. |
| `api_base` | no | Overrides the provider's default API origin. Required to reach a self-hosted forge (e.g. GitHub Enterprise). |
| `web_base` | no | Overrides the provider's default web origin, used for the item's browsable URL. Derived from `api_base` when omitted, so a self-hosted GHE source only needs to set `api_base`. |

**A self-hosted GitHub Enterprise example** — an internal repo behind a company GHE
instance, alongside the public `blizzard` source:

```toml
[[pm_source]]
name = "internal"
provider = "github"
repo = "acme/internal-tool"
token_env = "BZ_INTERNAL_TOKEN"
api_base = "https://git.corp.internal/api/v3"
web_base = "https://git.corp.internal"        # explicit override illustration only —
                                               # api_base alone is enough (web_base derives
                                               # from it); shown here so the override syntax
                                               # is visible somewhere in this doc.
```

`name = "internal"` is a free choice **only** because `acme/internal-tool` is a brand-new
source with no chunks minted against it yet. That freedom does not extend to a repo that
already has chunks in this hub — see the repo-tail rule in the upgrade note below, which
this example is not an illustration of.

### Credential indirection

`token_env` names an environment variable; the secret itself goes in the hub's env
file (`/etc/blizzard/hub.env` under the systemd layout above), never in
`blizzard-hub.toml` — the same separation the delivery forge's `BZ_FORGE_TOKEN`
already follows (D-084). An unset `token_env` fails at boot, naming the missing
variable rather than silently ingesting unauthenticated.

### The upgrade note

**An existing hub must add at least one `[[pm_source]]` block, or two things break
on the next deploy:**

- `GET /chunks/{id}/pm-items` 503s outright — "no PM work-source is configured" —
  until at least one source exists.
- Every chunk's board pointer label goes null: rendering `{source}#{ref}` needs a
  source name, and there is none to render until a source is configured.

This is not optional for a hub that already ingests PM items; there is no
backward-compatible default, because the PM source list also bounds which repos
the hub is willing to ingest from (see below). Add the `[[pm_source]]` block to
`blizzard-hub.toml` as part of the same maintenance window as the wheel upgrade,
before running `migrate`/restarting the daemon (see the install/upgrade steps above).

**For a repo that already has chunks in this hub, `name` is not a free choice — it
must be the repo's own tail** (the part after the last `/`; e.g. `blizzard` for
`paul-gross/blizzard`). The migration that introduced `[[pm_source]]` backfilled every
existing pointer's `source` to its repo tail, so a `name` that does not match strands
those pointers: nothing 503s (the hub sees a non-empty source list and boots clean),
but every pre-existing chunk for that repo silently degrades — `label` goes `null` and
its `pm-items` entry carries `error="no configured PM source named '<repo-tail>'"`,
because the pointer's `source` and the configured `name` no longer agree. A repo with
no chunks minted against it yet has no such constraint — any `name` is safe (the GHE
example above is exactly that case, not an illustration of the repo-tail rule).

**Verify you got it right** after the upgrade: for any chunk that existed before this
release, read its PM items and confirm no entry carries an `error`:

```
curl -s http://<hub>/api/chunks/<chunk_id>/pm-items | jq '.items[].error'
```

Every value printed should be `null`. A non-null `error` naming a PM source means the
configured `name` does not match the backfilled repo tail for that chunk's pointer —
fix the `name` (or add a second `[[pm_source]]` under the correct tail) and restart.

### Ingest tokens

`blizzard hub ingest` takes one or more source-native tokens and mints a chunk. Each
token is one of:

- `<source>:<ref>` — e.g. `blizzard:26`
- `<source>#<ref>` — e.g. `blizzard#26`
- a pasted PM item URL (e.g. the GitHub issue's own URL)

For the `github` provider, `<ref>` must be numeric (the issue number) — a `<source>:<ref>`
or `<source>#<ref>` token with a non-numeric `ref` (e.g. `blizzard:v2`) matches no
configured source's `parse` and surfaces as the same 422 an unconfigured repo gets ("not
claimed by any configured PM source"), which misdiagnoses as a missing `[[pm_source]]`
rather than a malformed ref.

The CLI carries no parsing of its own: it hands the token to the hub, which resolves
it against every configured source's own `parse`. The legacy `github:<rest>` prefix
is deprecated — it still resolves (warns on stderr, then passes `rest` on its own
merits) but carries no provider selection of its own anymore, since a token now
resolves against whichever configured source claims it.

### Unconfigured repos are a 422 at the front door

The configured source list is also the hub's allowlist of ingestable repos: a token
that names a repo (via URL or an unresolvable source name) that no `[[pm_source]]`
covers gets rejected with `422 Unprocessable Entity`, naming the token and the
sources that *are* configured. Adding a repo to the fleet means adding its
`[[pm_source]]` block first — there is no separate allowlist to keep in sync.

## The runner's two doors

The runner daemon serves one API on two listeners, and which one you address depends on
who you are:

| Client | Door | How it addresses it |
|--------|------|---------------------|
| the CLI's local verbs (`runner pause`, `runner start`) | `runner.sock`, mode 0600, in the runtime dir | `--dir` (or `$BZ_RUNNER_DIR`) — no port, no config file read |
| the runner's web app in a browser | the TCP port (`8431` by default) | same-origin `/api/*` on the page's own host |
| worker hooks (`heartbeat`, `ask`, …) | the TCP port | `BLIZZARD_RUNNER_URL`, injected into the spawn |

Same app, same routes — two doors, not two APIs. A browser cannot open a unix socket,
which is why the TCP listener exists; the socket exists because the operator's controls
should not depend on a port, and filesystem permissions are their access control (D-068).

**Run the local verbs as the service account.** The socket is mode 0600 and the unit runs
as `blizzard`, so the filesystem access control above is doing its job: another account —
including root's shell habits — is not the owner, and the verb fails with `EACCES`. Use
the same `sudo -u` form the install steps use:

```bash
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner pause --dir /var/lib/blizzard/runner
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner start --dir /var/lib/blizzard/runner
```

`--runner-url` (or `$BZ_RUNNER_URL`) points a local verb at the TCP door instead — for a
shell that cannot see the runtime dir, or cannot open the socket. Passing both `--dir` and
`--runner-url` explicitly is an error; an explicit flag beats either variable, and if both
arrive from the environment the socket wins (D-068's default transport). Note the two are
different namespaces: `$BZ_RUNNER_URL` is this operator setting, while
`BLIZZARD_RUNNER_URL` in the table above is spawn-injected worker identity the runner
mints per worker — setting one does not affect the other.

`runner pause` and `runner start` are pure clients of this API and never contact the hub,
so they keep working while it is unreachable. They set the runner's **own** brake, which
means something different from `blizzard hub pause <runner_id>`: the hub brake still just
stops new claims (in-flight chunks always run on); the runner's own brake means "start no
processes on this machine" — no new claims, but also no restart-resume, no requeue
respawn, and no judging a worker that exits while it's on, since judging one resumes its
session. Nothing is lost either way: a live worker already running is left alone (this is
not a drain), and every lease, route, and retry budget the brake defers is picked up
exactly where it left off once the brake clears — see `blizzard-runner pause --help` for
the full contract. Each brake is cleared only where it was set — `runner start` locally,
`blizzard hub resume` at the hub.

With no daemon running, the verbs report that rather than reading the store behind its
back — the store is reached only through the daemon that owns it, in every case. What you
see depends on how the daemon left:

| How it stopped | On disk | What a local verb reports |
|----------------|---------|---------------------------|
| `systemctl stop` / SIGTERM | the socket is unlinked on the way out | `no runner daemon is serving at …` — start one |
| `kill -9`, OOM, reboot | the socket file is left behind | a connection error against that path — nothing is listening on the corpse |

Either way the next `host` start is clean: it clears a socket nothing is serving, and
refuses to start beside one that is still live (the store is single-writer).

## Detaching a chunk

Detach forcibly releases a chunk's live route — the board's Detach control (chunk
detail dock, issue #42) and `blizzard hub detach <chunk_id>` both reach the same
`POST /api/chunks/{id}/detach`, so either door does exactly the same thing. It is
**not** requeue: no supersession fact is recorded and no epoch bumps, so a
`needs_human` chunk detached this way is still `needs_human` afterward — only the
route is gone, freeing the runner that held it. 409s when the chunk already has no
live route to release. See `blizzard hub detach --help` for the CLI's full contract.

## The recovery contract

Two systemd mechanisms combine to deliver the journey's "came back under systemd":

| Failure | What systemd does | What blizzard does on restart |
|---------|-------------------|-------------------------------|
| `kill -9`, OOM, or crash of a daemon | `Restart=always` brings it straight back (`RestartSec=2`) | Startup pass recovers from the durable store — see below |
| Machine reboot | The enabled units start at boot (`WantedBy=multi-user.target`) | Same startup pass, from the same on-disk store |
| Graceful restart (`systemctl restart`, or stop→start on a wheel upgrade) | The SIGTERM lets the daemon run its shutdown path *before* exiting; `Restart=`/boot then brings it back | The shutdown marks every in-flight lease with a durable resume-intent (D-082); the first tick **RESUMEs** each session in place — same lease/epoch/session, only the pid rewritten, no retry consumed — so **in-flight agent context is preserved**, not merely "not worked twice" |

The startup pass is where the "reaped the stale leases … continued at exactly the
node the hub last recorded" clause is honored, and it is **not** new code — it is
the loop's normal first move — **provided the runner's own brake (`runner pause`,
issue #45) is off.** If it is on, the runner's first tick(s) after a restart still run
REAP and RESUME, but a stalled worker is not killed and a marked session is not
re-attached — both wait, exactly where the crash or the shutdown left them, for the
first tick after `runner start` clears the brake. Nothing described below is lost in
the meantime, only deferred.

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

`runner pause`, then `systemctl restart` to adopt a new wheel, is a plausible
maintenance sequence — but a runner paused *before* the restart stays paused after it
(the brake is a durable fact, not daemon state), so its marked sessions sit un-resumed
until `runner start` is run too. Pause to stop new work landing mid-upgrade, then
start again once the new wheel is confirmed healthy, the same way you would leave it
paused across any other maintenance window.

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

The full sweep — the same recovery asserted at every boundary the crash-point
registry enumerates (`discover_crash_points`; see `bzh:crash-point-registry`), including
a `kill -9` *mid-RESUME* at each graceful-restart boundary
(`test_kill9_at_resume_crash_point`) and mid-abandon at each detach boundary
(`test_kill9_at_abandon_crash_point`) — is `mise run crash-sweep`, and the tag
`release` workflow runs it in CI. The unit files themselves are guarded by
`tests/test_systemd_units.py`, which holds their `ExecStart` to the real shipped
entry points and asserts the `Restart=` and boot-enable directives this contract
depends on.
