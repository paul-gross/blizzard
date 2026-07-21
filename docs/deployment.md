# Deployment and boot recovery

How a colocated blizzard machine — one hub and one supervisor (runner) side by
side — is installed under systemd, and the contract that makes it survive a crash
or a reboot with nothing lost and nothing worked twice. This is the operator
reference for the following journey:

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
personalities of the one `blizzard` wheel, so there is no version skew
between them and no Node at install or runtime:

- **hub** — `blizzard-hub host`: the fleet's HTTP API, SSE, and the embedded
  mission-control board. Holds the forge base URL and PM credentials
  — those live only here, never on the runner.
- **supervisor (runner)** — `blizzard-runner host`: the stateless
  `REAP → PULL → FILL → ADVANCE` loop behind a machine-local API. Reaches the hub
  outbound-only, so it keeps working while the hub is briefly unreachable — every
  such call carries the runner's enrolled bearer token (see "Runner authentication"
  below).

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

# 3. Seed each runtime dir: config scaffold + data dir + a store migrated to head.
#    Idempotent — safe to re-run.
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-hub    init /var/lib/blizzard/hub
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner init /var/lib/blizzard/runner

# 4. Point the hub at the forge and the runner at its workspace. The hub's
#    delivery credentials go in /etc/blizzard/hub.env (BZ_FORGE_URL, BZ_FORGE_TOKEN, …);
#    its PM work sources are declared in blizzard-hub.toml's [[pm_source]] blocks
#    (init scaffolds a commented-out example — see "Configuring PM work sources"
#    below); the runner's workspace/harness bindings live in its own config.toml,
#    written by `init` and edited in place (no credentials).

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
store, so a wheel that ships a new schema revision reconciles the on-disk store
to head on the next start; the daemon refuses to start on a revision mismatch, so a
forgotten migration fails loudly rather than corrupting state. A graceful `systemctl
restart` also preserves in-flight work across the upgrade — see the recovery contract
below. That loud-failure guarantee is the whole safety story for a revision whose
`upgrade()` only adds or backfills; it is not for a **destructive** one, whose
`upgrade()` deletes rows outright — see "The pr-opened-idempotent upgrade note" below for the one
revision so far that does.

### The pr-opened-idempotent upgrade note

**`20260716_2206_hub_pr_opened_idempotent` is the first migration in either store whose
`upgrade()` deletes rows** (the escalation-takeover and graph-node-produces-checks revisions are the
only other destructive revisions in either tree, and both only drop columns). Closing a coordinator read-then-write race
(issue #10) with a unique constraint on `(chunk_id, repo)` first requires a store
carrying the race's duplicate rows to no longer carry them, so `upgrade()` deletes every
`delivery_pr_opened` row but the earliest per `(chunk_id, repo)` before adding the
constraint. `downgrade()` only drops the constraint back — it does not restore the
deleted rows; they are gone for good.

In practice this only ever removes true duplicates (a redundant `pr.opened` fact for a
PR the forge had already deduplicated to one), so no chunk loses a fact a human or the
board ever relied on distinguishing. But because the delete is unconditional and
irreversible, **copy the hub's store file before restarting into a wheel carrying this
migration** — `cp <hub-dir>/data/hub.db <hub-dir>/data/hub.db.pre-pr-opened-idempotent` for the sqlite
default, or the equivalent for a configured postgres `db_url` (`bzh:sql-portable`) —
the same caution any one-way migration deserves, and not something `migrate`'s
revision-mismatch guard can catch after the fact, since the delete is exactly what
reaching that revision means.

## Naming the runtime directory

Every verb that takes a runtime dir resolves it from three rungs, highest to lowest: the
explicit flag or argument, then an environment variable, then the current working
directory. `init` and `host` accept a positional `DIRECTORY` as well as `--dir`; passing
both requires they agree, and a genuine command-line conflict exits non-zero naming both
values. `migrate`, `runner tick`, `runner pause`, and `runner start` take `--dir` only.

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

The hub's PM pass-through reads every chunk's PM item through a
**configured PM work source** — a named, credentialed binding to one forge repo, declared
as an `[[pm_source]]` table in `blizzard-hub.toml`. This is a separate seam from the
delivery forge above: `BZ_FORGE_URL`/`BZ_FORGE_TOKEN` in the hub's env file control where
a chunk's PR is opened and landed; `[[pm_source]]` controls where its PM item is *read
from*, and each source carries its own credential rather than sharing the
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
| `repo` | yes | The `owner/name` coordinate this source is pinned to. Each `(provider, repo)` pair may appear under only one `name` — two names for the same repo would let one item be ingested twice under two identities. |
| `token_env` | yes | Names an environment variable — **not the secret itself**. See "Credential indirection" below. |
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
already follows. An unset `token_env` fails at boot, naming the missing
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

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk. Each
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

## Runner authentication

This is **machine identity** — a runner authenticating itself to the hub — distinct
from the **human login** plane ("Human authentication (OAuth login)" below), which
authenticates an operator to the hub's own web/API surface.

Two independent rollout flags gate the fleet's runner-identity and route-capability
defenses, both scaffolded into `blizzard-hub.toml` by `blizzard hub init`, both
defaulting to `warn`:

| Flag | Guards | `warn` (default) | `enforce` |
|------|--------|-------------------|-----------|
| `runner_auth_mode` | every fleet-router call's bearer token resolves to a known runner identity | logs a missing/invalid/mismatched token and lets the call proceed | rejects it (401/403) |
| `route_token_mode` | the per-acquisition route capability token presented on every chunk-scoped write | logs a missing/mismatched route token and lets the write proceed | rejects it as a semantic failure |

They are independent on purpose — a fleet can flip one on before the other — and
neither has any effect while `warn`; a fresh deploy or an upgraded hub keeps working
unauthenticated until an operator deliberately tightens them.

**Enrollment requires the runner to have registered first.** A runner registers
itself with the hub on its own pull; `blizzard hub runner enroll <runner_id>` 404s
naming the unknown id until that has happened at least once. Enrollment is a
deliberate operator act on a runner the fleet already knows, not a
trust-on-first-use grant to a name nobody has registered yet.

The rollout sequence, in order:

1. Start the runner once so it registers with the hub.
2. `blizzard hub runner enroll <runner_id>` — mints (or, run again, rotates) the
   runner's bearer token and prints the plaintext exactly once; there is no way to
   read it back later, only to rotate it.
3. Install that token in the runner's own runtime env file (the systemd
   `EnvironmentFile`, or the shell env a manually-run runner inherits) under the
   variable its `token_env` config key names — see "The runner's outbound token"
   below.
4. Flip `runner_auth_mode` to `enforce` in `blizzard-hub.toml` and restart the hub,
   once every runner in the fleet carries an enrolled token.
5. Flip `route_token_mode` to `enforce` only after outbound buffers carrying
   pre-upgrade, token-less facts have drained — `warn` already covers that window,
   so there is no separate grace period to wait out beyond it.

### The runner's outbound token

`blizzard-runner.toml`'s `token_env` (default `BZ_HUB_TOKEN`) names the environment
variable carrying the runner's enrolled bearer token — never the secret itself,
mirroring the `[[pm_source]] token_env` indirection above. The secret goes in the
runner's runtime env file (e.g. `/etc/blizzard/runner.env` under the systemd layout,
declared as that unit's `EnvironmentFile`), read once at config load. Every outbound
runner→hub call — the reconciliation loop's `httpx.Client` and the pm-items proxy
alike — attaches it as `Authorization: Bearer <token>`; an unenrolled runner (or one
whose env file has not been updated yet) attaches nothing, and `runner_auth_mode`
above decides whether the hub tolerates that.

### Forwarding extra vars to workers

`blizzard-runner.toml`'s `[worker] env_passthrough` is the operator's lever to widen
the fixed base allowlist (`PATH`/`HOME`/`USER`/`LANG`/`LC_*`/`TERM`/`TMPDIR`) every
worker/judge/resume child process is built from — name a variable there to forward it
into every spawn too. Empty (the fresh-scaffold default) means the base allowlist
only; a daemon credential such as `BZ_HUB_TOKEN` is never in scope for this list, so
it is absent from a worker child by construction unless deliberately named here.

### The worker spawn preamble

Every worker's spawn prompt is three ordered layers ahead of the node's own envelope
prompt: (1) a baked-in blizzard preamble — always present, framing the worker as
operating inside the fleet and naming its worker-facing `blizzard runner` verbs
(`ask`, `pm-items`) — (2) the operator's own `workspace_prompt` prose, layered on top
when set, and (3) a machine-local facts table (runner/chunk/lease identity, held
environment(s)).

Layer 1 is overridable but never absent: `blizzard-runner.toml`'s `runner_prompt`
(inline text) or `runner_prompt_file` (a path, wins over inline text when both are
set) — or `BZ_RUNNER_PROMPT` seeding a fresh scaffold — replaces the baked default
wholesale when set; unset, the baked default renders. Both are config/startup knobs,
resolved once at `host` startup — unlike `workspace_prompt`, which also has a live
`PUT /api/workspace-prompt` override, `runner_prompt` has no runtime door, so changing
it means restarting the runner. A `runner_prompt_file` naming a path that does not
exist raises a `ConfigError` at startup, the same fail-fast the workspace-prompt
file knob already gives.

## Human authentication (OAuth login)

Distinct from "Runner authentication" above: this plane authenticates an **operator**
logging into the hub's own web/API surface, not a runner authenticating itself to the
hub. The hub's `[auth]` table (scaffolded into `blizzard-hub.toml` by `blizzard hub
init`) is the human-auth rollout knob:

```toml
[auth]
mode = "none"                    # "none" (the shipped default) or "oauth"
# superuser = "ada@example.com"  # the bootstrap superuser's email — see below

# [[auth.oauth.provider]]
# name = "github"                    # the provider's identity; identities key on it
# type = "github"                    # "github" or "oidc"
# display_name = "GitHub"            # the login button's label
# client_id = "..."                  # the OAuth app's client id
# client_secret_env = "BZ_OAUTH_GITHUB_SECRET"  # names an env var — the secret itself
#                                                 # lives in this runtime's env file
# issuer = "https://accounts.example.com"        # oidc only: the discovery issuer
# api_base = "https://ghe.example.internal"       # optional: override the provider's
#                                                  # default host (github type only)
```

`mode = "none"` (the shipped default) resolves every request to the implicit
operator/superuser identity with no store read — a fresh or upgraded hub keeps working
unauthenticated until an operator deliberately opts in. `mode = "oauth"` activates the
session/permission seam and requires at least one `[[auth.oauth.provider]]` entry.
`type` selects the conformer: `"github"` (an OAuth App) or `"oidc"` (a generic OIDC
issuer, discovered via `<issuer>/.well-known/openid-configuration`). `client_secret_env`
mirrors `[[pm_source]] token_env`'s indirection exactly — it names an environment
variable, never the secret itself; the secret goes in the hub's runtime env file (e.g.
`/etc/blizzard/hub.env` under the systemd layout above), a deployment credential like
`BZ_FORGE_TOKEN`/`BZ_PM_TOKEN` above.

### The superuser bootstrap

`[auth].superuser` names one email as the fleet's bootstrap identity, ensured at every
hub boot: once a verified login matches that email, the hub promotes that user to
`superuser`; until then, the intent is pre-provisioned and unclaimed, and the boot log
(plus an `auth_facts` entry) surfaces that on every restart rather than failing
silently. Changing `superuser` to a different email demotes whichever user the
previous target had claimed back to `admin` — at most one user is ever the bootstrapped
superuser at a time, and this is the *only* way a user becomes (or stops being)
`superuser`; the role is never assignable through the admin API.

### Roles, in one paragraph

A hub-local user carries one of four roles, a total order —
`guest < contributor < admin < superuser`. A freshly-logged-in identity lands as
`guest`: the lobby, holding no permissions at all beyond the public self routes
(`GET /api/me`, login, logout) — no board read, no writes. An `admin` (promoted from
the admin page, `POST /api/users/{id}/role`, gated on `user:manage`) can move a subject
between `guest` and `contributor` freely, but only a `superuser` actor may grant or
revoke `admin` itself, and `superuser` is never assignable through that API in either
direction — it is bootstrap-only, per the previous section.

### Operator verbs

`blizzard hub login` logs an operator into the hub: by default it opens a browser to
the hub's own authorize endpoint (PKCE, an ephemeral `127.0.0.1` loopback redirect) —
the user completes login *at the hub*, and the resulting session token is stored
locally. `--paste` swaps that for the paste-code fallback (the hub renders a short
one-time code the user pastes back into the prompt), for a headless/remote shell with
no reachable loopback listener. `blizzard hub logout` deletes the locally stored
session and revokes it at the hub, so it stops resolving even if it leaked.
`blizzard hub rotate-signing-key` rotates the hub's IdP signing keypair — mints a fresh
current key, demoting the old current to previous; runners pick up the new key by
re-fetching JWKS on an unknown `kid`, no restart needed. Under `mode = "oauth"`,
`rotate-signing-key` is itself gated on `user:manage` and requires a logged-in session.

### Runner-side federation

A runner that wants its own human web surface reachable via the hub's SSO bounce
declares `public_url` in `blizzard-runner.toml` — its own browser-reachable base URL,
from which the runner derives the one redirect URI it presents to the hub's IdP
authorize endpoint (`<public_url>/api/auth/callback`). Empty (the fresh-scaffold
default) means this runner registers no federation identity, so its human web surface
stays unreachable via SSO — and, since there is no IdP to bounce to either way, that is
also the correct state when the hub itself runs `auth.mode = "none"`.

Runner-local role resolution is a separate `[auth]` table, living only on the runner —
never in the hub store or its admin page:

```toml
[auth]
# superuser = "<hub-username>"   # this runner's own sovereign, config-only
hub_role_default = "mirror"      # "mirror" (reproduce the hub's own role claim) or a
                                  # fixed cap ("contributor"/"guest")

[auth.users]
# ada = "admin"                  # per-hub-username role overrides
```

`superuser` names a hub **username** as this runner's own sovereign — never assignable
through a JWT claim, a config-only designation mirroring the hub's own `auth.superuser`
bootstrap identity. `hub_role_default` is the fallback runner-local role for a hub
identity with no `[auth.users]` override: `"mirror"` (the default) trusts the hub's own
`role` claim verbatim, or a fixed cap (`"contributor"`/`"guest"`) floors every unmatched
identity regardless of hub role. `[auth.users]` overrides that default per hub
username, resolved from the JWT's `username` claim only (never `email`, which is
mutable and may be null).

## Produces-artifact enforcement

`produces_mode` is a third rollout flag, scaffolded into `blizzard-hub.toml` by
`blizzard hub init` alongside `runner_auth_mode`/`route_token_mode` above and defaulting
to `warn` the same way — but it guards a different concern: not runner identity or
route capability, a node's own `produces:` declaration. A node that lists a name in
`produces:` should carry either a pushed git commit of that name or an explicit
`blizzard runner attach --name <name>` for it; a name backed only by the worker's
judgement-assessment fallback is not proof the worker produced the thing the graph
asked for.

| Flag | Guards | `warn` (default) | `enforce` |
|------|--------|-------------------|-----------|
| `produces_mode` | every `produces:` name has an explicit attachment or a covering git commit | logs the missing names and lets the completion proceed on the assessment fallback | rejects the completion as a semantic failure |

It is independent of `runner_auth_mode`/`route_token_mode` — flipping it does not
depend on either of them, and vice versa — so it is not part of the rollout sequence
above. A fresh deploy or an upgraded hub keeps accepting assessment-fallback
completions until an operator deliberately flips it to `enforce` in
`blizzard-hub.toml` and restarts the hub.

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
should not depend on a port, and filesystem permissions are their access control.

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
arrive from the environment the socket wins (the default transport). Note the two are
different namespaces: `$BZ_RUNNER_URL` is this operator setting, while
`BLIZZARD_RUNNER_URL` in the table above is spawn-injected worker identity the runner
mints per worker — setting one does not affect the other.

`runner pause` and `runner start` are pure clients of this API and never contact the hub,
so they keep working while it is unreachable. They set the runner's **own** brake, which
means something different from `blizzard hub runner pause <runner_id>`: the hub brake still just
stops new claims (in-flight chunks always run on); the runner's own brake means "start no
processes on this machine" — no new claims, but also no restart-resume, no requeue
respawn, and no judging a worker that exits while it's on, since judging one resumes its
session. Nothing is lost either way: a live worker already running is left alone (this is
not a drain), and every lease, route, and retry budget the brake defers is picked up
exactly where it left off once the brake clears — see `blizzard-runner pause --help` for
the full contract. Each brake is cleared only where it was set — `runner start` locally,
`blizzard hub runner resume` at the hub.

The local brake has one **non-operator** trigger too: a configured runner spend ceiling
engages this same brake automatically when the fleet's rolling-window spend crosses it (see
"Bounding fleet spend" below). It is the identical brake — same "start no processes on this
machine" semantics, live workers left to finish — so a runner can come back `[paused: local]`
with no `runner pause` ever issued. It clears the same way, and *only* that way: an explicit
`runner start`. `blizzard hub status` names the reason on a ceiling-engaged runner so you can
tell it apart from a hand-issued pause.

With no daemon running, the verbs report that rather than reading the store behind its
back — the store is reached only through the daemon that owns it, in every case. What you
see depends on how the daemon left:

| How it stopped | On disk | What a local verb reports |
|----------------|---------|---------------------------|
| `systemctl stop` / SIGTERM | the socket is unlinked on the way out | `no runner daemon is serving at …` — start one |
| `kill -9`, OOM, reboot | the socket file is left behind | a connection error against that path — nothing is listening on the corpse |

Either way the next `host` start is clean: it clears a socket nothing is serving, and
refuses to start beside one that is still live (the store is single-writer).

## Chunk and runner control verbs, two axes — pause, stop, or detach a chunk; pause a runner (hub or local)

Five verbs stop work, and two of them share the word "pause," which is exactly where
operators mix them up. The three chunk-level verbs split along what they do to the
claim: keep it (`chunk pause`), give it away (`detach`), or end it for good (`stop`).

- **`blizzard hub chunk pause <chunk_id>` / `chunk resume <chunk_id>`** (issue #46), or
  the board's **Pause**/**Resume** control in the chunk detail dock beside Detach —
  targets **one chunk**. On a chunk with a live claim, the runner kills that chunk's
  live worker but **keeps the claim**: the lease, route, epoch, held environments, and
  retry budget all survive untouched — only the process dies. Pause is also allowed on
  a chunk that hasn't been claimed yet (`ready`): there it holds the chunk out of the
  queue instead — it derives `paused`, not `ready`, so FILL skips it until it's
  resumed. `chunk resume` respawns a parked session **in place**, under the unchanged
  lease/epoch/session id, consuming no retry (a still-unclaimed chunk just re-derives
  `ready` and rejoins the queue). Refused (`409`) on a chunk that is
  `done`/`stopped`/`delivering`; deliberately **allowed** on
  `waiting_on_human`/`needs_human` — pause is a broad lever. (The `stopped` case in
  that refusal list — see below — was inert until `stop` existed to reach it.) The
  pause *fact* survives the answer to that question untouched (answering never
  un-pauses a chunk), but the *derived status* doesn't show `paused` while the
  question is open — a chunk both paused and parked on a question derives
  `waiting_on_human` first, so the board shows a `waiting_on_human` chip, not
  `paused`, until the question is answered. The dock still says so plainly and still
  offers **Resume** there — it reads the pause fact (`ChunkDetail.pause`), not the
  chip. Once answered, the pause fact is still there, so the chunk then derives
  `paused` (and stays parked) rather than resuming — `chunk resume` is what actually
  lets it go. `chunk resume` is idempotent — resuming an already-running chunk is a
  harmless no-op.
- **`blizzard hub chunk detach <chunk_id>`**, or the board's **Detach** control in the
  chunk detail dock (issue #42) — also targets **one chunk**, but the opposite direction:
  it **gives the claim away**. Both doors reach the same `POST /api/chunks/{id}/detach`,
  so either does exactly the same thing. The route is released, every held environment is
  freed, the lease closes, and the chunk re-derives `ready` so any runner — including a
  different one — can claim it next. Any live worker is abandoned along with everything
  else, not merely killed-and-kept. It is **not** requeue: no supersession fact is
  recorded and no epoch bumps, so a `needs_human` chunk detached this way is still
  `needs_human` afterward — only the route is gone. Refused (`409`) when the chunk has no
  live route left to release. See `blizzard hub chunk detach --help` for the CLI's full
  contract.
- **`blizzard hub chunk stop <chunk_id>`** (issue #118) — CLI/API only, with no board
  control today; there is no Stop button in the chunk detail dock the way Pause and
  Detach each have one, only `POST /api/chunks/{id}/stop`. Terminal and
  **irreversible** — there is no `un-stop`. It does **both** of what `chunk pause` and
  `detach` each do half of: it writes the terminal `chunk.stopped` fact *and* releases
  any live route, so the holding runner frees the environments on its own next tick —
  no separate `detach` call needed. Unlike `detach`, a live route is not required:
  stop is allowed on `not_ready`, `ready`, and an already-detached chunk alike — the
  route release is conditional, not required. Refused (`409`) only when the chunk is
  already `done` or `stopped` — not retroactive un-delivery, and not a lever for
  clearing a `delivering`/`waiting_on_human`/`needs_human` chunk back to a fresh
  state, only for ending it. See `blizzard hub chunk stop --help` for the CLI's full
  contract.
- **`blizzard hub runner pause <runner_id>` / `runner resume <runner_id>`** (the hub brake)
  and **`runner pause` / `runner start`** (the runner's own local brake, issue #45,
  above) are **per-runner**, not per-chunk. Neither kills any particular chunk's
  worker: the hub brake only stops that runner from claiming *new* work (every
  in-flight chunk, live worker included, runs on); the local brake additionally blocks
  every other spawn site (restart-resume, an answer-resume, a requeue respawn, …) but
  still never kills a worker that is already running — pausing locally is not a drain.

The distinction worth holding onto: `chunk pause` is the **only** one of the three
chunk-level verbs that kills a live worker while **keeping** the claim — `detach` and
`stop` both give it away (or end it), they just differ in whether the chunk can be
reclaimed afterward. The two runner-level brakes sit apart from all three: they never
touch a live worker, and they have no notion of "this one chunk" at all.

**A pause-parked chunk still occupies an agent slot.** FILL only ever claims new work
into a runner's *open* slots, and a chunk pause deliberately leaves the lease active
and its environments held warm for the resume — that is what makes the resume land in
place instead of re-provisioning. So a paused lease counts against `max_agents`
exactly like a running one, with no worker consuming it. Pause enough chunks on one
runner and it silently stops claiming new work — no error, nothing beyond the pause's
own log line — because every slot is spoken for by parked claims. Detach and stop, by
contrast, each free the slot immediately (the claim is given away, or ended, not held).

A restart into a **standing** chunk pause does not resume it — the runner checks the
pause fact first, ahead of the normal restart-resume path described below (see "The
recovery contract"), so a chunk still marked paused when the runner comes back is
(re-)parked, not respawned. The claim is kept exactly as it would be if the pause had
landed on a live tick; only a chunk that was *not* paused resumes in place on restart.

### Editing an unclaimed chunk's build config

While a chunk sits **unclaimed** — resting `not_ready` (minted but not yet promoted)
or promoted to `ready` with no runner holding it yet — its pinned **graph** and
**model** are editable, both from the chunk detail dock and via `POST
/api/chunks/{id}/graph` / `POST /api/chunks/{id}/model`, or together via `PATCH
/api/chunks/{id}` (below). Issue #120 widened this past its original `not_ready`-only
window (issue #27): the wrong graph is often noticed only after promote, with no
runner anywhere near the chunk yet, so a promoted-but-unclaimed chunk stays
repinnable. Once the chunk is **claimed or later** — `running`, `delivering`,
`waiting_on_human`, `needs_human`, `paused` (post-claim), `done`, or `stopped` — both
edits are refused with `409`.

`PATCH /api/chunks/{id}` (issue #124) applies any of `graph_id`, `model`, and
`intended_migration` in one request, all-or-nothing: if any supplied field is outside
*its own* editable window, the whole request is refused (`409`, naming the field) and
nothing in the body is applied. `graph_id`/`model` share the unclaimed-only window
above; `intended_migration` — see "Migrating a claimed chunk to another graph" below —
is different: it is editable at **any non-terminal status**, claimed or not, so a
`PATCH` naming it alongside a claimed chunk's now-sealed `graph_id` still refuses the
whole request on `graph_id`.

A graph edit has a second, distinct `409`: targeting a graph that has been
**retired** (see "Graph lifecycle — retire and re-enable" below) is refused even on an
otherwise-editable chunk, naming the retired graph id rather than the chunk's status.

### Migrating a claimed chunk to another graph

`blizzard hub chunk migrate <chunk-id> --to-graph <graph> [--node <name>] [--cancel]`,
or `PATCH /api/chunks/{id}` `intended_migration` (issue #124) — sets a **standing
intent** to move a chunk onto another graph, consulted (never applied eagerly) at the
chunk's *next* transition. Unlike the stop-work verbs above, it does not stop or
interrupt any in-flight work: the current attempt runs to its normal verdict, and only
that transition either fires the intent or, for `auto` mode with no name match, leaves
it set for the transition after. `--to-graph` names a graph id or a graph name
resolved to the newest enabled graph of that name; a blank name, a retired target, or
a target equal to the chunk's own current pin is refused (`409`). With no `--node`,
the intent is `auto`: it fires only when the transition's own destination node name
also exists on the target graph, landing there; with no name match the transition
applies unchanged and the intent stays set for next time. `--node <name>` makes it
`forced`: it fires unconditionally at the next transition, landing on the named node
regardless of the transition's own destination — refused (`409`) up front if that node
does not exist on the target graph. `--cancel` (or `PATCH` with `intended_migration:
null`) clears a standing intent without firing it.

Editable at **any non-terminal status** — `not_ready` and `ready` too, not just once
claimed — since the intent is a plain mutable chunk property, not a transition itself;
it is only ever *consulted* at a transition, which is why in practice it matters once
a chunk is claimed and progressing, and why it complements rather than replaces the
pre-claim graph repin above. When the intent fires, the chunk's movement is recorded
as a migration exactly like an authored cross-graph judgement choice (see "Graph
lifecycle" below): it re-pins the chunk's graph, lands it on the resolved node, and
clears the intent in the same write. Landing governs by the landed node's own
executor — a migration landing on a hub-executed node derives `delivering`, exactly as
a transition into one does. See `blizzard hub chunk migrate --help` for the CLI's full
contract.

## Graph lifecycle — retire and re-enable

`blizzard hub graph list` / `graph retire <graph_id>` / `graph enable <graph_id>`
(issue #101), or the graph explorer's own **Retire** / **Re-enable** buttons and
lifecycle badge in the web board — an operator's brake over which graph a **name**
resolves to. Not a work-stopping lever like the four verbs above: a graph carries no
chunk, no claim, no live worker to interrupt. Retiring never touches the graph's own
immutable row — it appends a `graph.retired` fact, reversed by `graph enable`'s
`graph.enabled` fact — so the brake is **reversible**, and every toggle is itself an
append-only audit trail rather than a destructive edit.

**What retiring changes, and what it deliberately leaves alone.** A chunk that
already pins a retired graph keeps running it to completion — existing pins are left
to run out; issue #101 is scoped to blocking only *new* resolution by name, never
touching a chunk mid-workflow. What a retire blocks is every name lookup: the
default-graph pin at ingest and a cross-graph migration's `graph:<name>` judgement
target both resolve through the newest **non-retired** graph of that name, skipping
every retired `graph_id` entirely.

**Retiring every version of a name is a real trap, not a hypothetical.** If every
graph ever minted under one name — including the packaged `default-delivery` the hub
ingests against out of the box — is retired, name resolution has nothing left to hand
back. The next ingest that would otherwise lazily mint a fresh copy of the packaged
default **refuses with `503`** instead: minting a fresh copy there would be
immediately effective and would silently undo the retire the moment it landed,
including across a hub restart. Re-enable one of the retired versions, or mint a new
graph under that name, to clear it. A cross-graph migration choice naming an
all-retired target has the same "nothing left to resolve" shape at the moment a
chunk takes it.

## Bounding fleet spend — cost caps and the spend kill-switch

An unattended overnight fleet spends against the operator's harness billing with no ceiling
by default. Two optional caps bound it, both configured in a `[cost]` table in
`blizzard-runner.toml` and both **absent by default — no `[cost]` table means no cap and no
ceiling, exactly the prior behavior**. Cost figures are the harness's own `total_cost_usd`;
blizzard maintains no pricing table and never fabricates a cost.

```toml
[cost]
# Per-chunk spend cap. When a chunk's total cost crosses this, it parks needs_human
# at its next step boundary. Absent = no per-chunk cap.
chunk_cap_usd = 5.0

# Runner spend ceiling over a rolling window. When this runner's spend across the
# trailing window crosses this, the local pause brake engages. Absent = no ceiling.
runner_ceiling_usd = 50.0

# The rolling window the ceiling sums over, in hours. Defaults to 24.0; only
# consulted when runner_ceiling_usd is set.
window_hours = 24.0
```

- **Per-chunk cap (`chunk_cap_usd`).** Checked **between attempts**, never by killing a live
  worker: when a chunk's derived total cost reaches the cap, the runner parks it `needs_human`
  at the next step boundary with an escalation naming the cap and the spend, and the usual
  takeover command to resume. A capped chunk is not a failed one — no retry is consumed.
  Resuming is a human decision: raise or clear the cap, then requeue the chunk and it proceeds.
- **Runner ceiling (`runner_ceiling_usd`, `window_hours`).** Checked at each tick: when this
  runner's spend over the trailing `window_hours` crosses the ceiling, the runner's **local
  pause brake** engages (the same brake `runner pause` sets — every spawn site suppressed, no
  retries consumed, live workers left to finish) and an escalation records the ceiling and the
  spend. The window is a rolling last-N-hours sum; **it does not auto-unpause** when the window
  later rolls the spend back under the ceiling. Clearing the brake is an explicit operator
  decision — `blizzard runner start`, exactly as for a hand-issued pause. `GET /api/runners`
  and `blizzard hub status` surface the ceiling reason on the paused runner, so it reads
  differently from a manual pause.
- **Cost-absent rows are a conservative lower bound.** When a worker crashes or is `kill -9`ed
  before the harness emits its final usage envelope, blizzard records the attempt's tokens from
  the session transcript but its **cost is genuinely unknown** — so an absent-cost row
  contributes its tokens but **$0** to the cost sum, making the total a lower bound, flagged
  **PARTIAL** wherever it is shown (a `~` marker on the board and in `hub status`). Both caps
  trip on this lower bound and surface PARTIAL in the escalation, so an operator knows the true
  spend may be higher — a cap never silently under-counts a crash-heavy chunk into looking cheap.

See `blizzard hub status` for the per-chunk cost column, the fleet total, and a paused runner's
ceiling reason; the board's chunk cards and detail dock show the same figures live.

## The recovery contract

Two systemd mechanisms combine to deliver the journey's "came back under systemd":

| Failure | What systemd does | What blizzard does on restart |
|---------|-------------------|-------------------------------|
| `kill -9`, OOM, or crash of a daemon | `Restart=always` brings it straight back (`RestartSec=2`) | Startup pass recovers from the durable store — see below |
| Machine reboot | The enabled units start at boot (`WantedBy=multi-user.target`) | Same startup pass, from the same on-disk store |
| Graceful restart (`systemctl restart`, or stop→start on a wheel upgrade) | The SIGTERM lets the daemon run its shutdown path *before* exiting; `Restart=`/boot then brings it back | The shutdown marks every in-flight lease with a durable resume-intent; the first tick **RESUMEs** each session in place — same lease/epoch/session, only the pid rewritten, no retry consumed — so **in-flight agent context is preserved**, not merely "not worked twice" (unless the lease is under a standing operator chunk pause — see below) |

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
  last-recorded node — never re-run from the start. Facts are the only truth,
  so a restart reads exactly the state a clean shutdown would have left.
- **Hub.** A completion re-flushed after a hub crash is applied idempotently
  behind the epoch fence, and a per-repo land already recorded is skipped
  on redelivery — so a crash mid-delivery lands the chunk exactly once, not twice.

A **graceful** restart does one better than reaping. Because the SIGTERM lets the
supervisor run a shutdown pass before it exits, it marks every in-flight lease with
a durable *resume-intent* instead of leaving its workers to be reaped. The
first tick after the restart then **RESUMEs** each marked session in place — the same
lease, epoch, and session, only the process id rewritten and no retry consumed — so a
`systemctl restart` (for example, to adopt a freshly-merged runner wheel) continues
each agent mid-thought rather than reaping and re-running it from the top —
**provided the chunk isn't under a standing operator pause** (issue #46; see "Four
verbs, two axes" above). If it is, the RESUME path re-parks it instead of respawning
it, the same way it would if the pause had landed on a live tick; the pause fact, not
the restart, decides. An ungraceful `kill -9` skips the marking, so its workers fall
back to the reap path above; and a crash *during* the re-attach itself degrades to
that same reap path — the resume is bounded by the crash-point sweep's recovery, no
stronger.

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

The behavior above is exercised end-to-end by the two **whole-process** cases of
the kill-9 crash sweep, plus the hub's own whole-process case for a generic hub
command node's delivery. They *are* the recovery demo: each runs the real
`build → deliver` scenario with the hub and runner as real subprocesses, then
restarts a whole daemon from the same store directory (systemd's job, done by hand
in the test) and asserts the chunk still converges and lands **exactly once**, with
the facts-level invariant checker green after the crash and again after recovery:

- `tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_mid_flight` — `kill -9`s
  the **supervisor** mid-flight; the restart's REAP reaps the stranded lease and
  the chunk converges.
- `tests/crash/test_kill9_sweep.py::test_kill9_at_hub_command_node_crash_point[hubnode.after-step.before-marker]`
  — `kill -9`s the **hub** mid-delivery, inside a generic hub command node's
  per-step window; the restart re-drives the executor off the re-flushed build
  completion and the change lands once.
- `tests/crash/test_kill9_sweep.py::test_graceful_restart_resumes_in_flight_session`
  — **gracefully** restarts the supervisor while a worker is in flight; the shutdown
  marks the lease and the restart RESUMEs the *same* session in place, so the
  chunk lands once without re-running from the top.

Run just the demo (needs the sibling `blizzard-mock` worktree and a local winter
source — see the crash-sweep header):

```bash
BLIZZARD_CRASH_SWEEP=1 uv run pytest \
  tests/crash/test_kill9_sweep.py::test_kill9_runner_daemon_mid_flight \
  "tests/crash/test_kill9_sweep.py::test_kill9_at_hub_command_node_crash_point[hubnode.after-step.before-marker]" \
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
