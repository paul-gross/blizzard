# Deployment and boot recovery

How a colocated blizzard machine — one hub and one supervisor (runner) side by side — is installed under systemd, and
the contract that makes it survive a crash or a reboot with nothing lost and nothing worked twice. This is the operator
reference for the following journey:

> At some point in the night the machine rebooted. It didn't matter: the supervisor and the colocated hub came back
> under systemd, the supervisor reaped the stale leases, re-read the environment bindings from its store, and continued
> — every chunk still at exactly the node the hub last recorded.

The two units live in [`packaging/systemd/`](../packaging/systemd/):
[`blizzard-hub.service`](../packaging/systemd/blizzard-hub.service) and
[`blizzard-runner.service`](../packaging/systemd/blizzard-runner.service).

## The colocated topology

One machine runs both daemons. Colocation is a choice, not a constraint — a runner on another machine points at the hub
the same way, and [`docs/remote-runner.md`](./remote-runner.md) walks that shape. Side by side they are two
personalities of the one `blizzard` wheel, so there is no version skew between them and no Node at install or runtime:

- **hub** — `blizzard-hub host`: the fleet's HTTP API, SSE, and the embedded mission-control board. Holds the forge base
  URL and work-source credentials — those live only here, never on the runner.
- **supervisor (runner)** — `blizzard-runner host`: the stateless `REAP → PULL → FILL → ADVANCE` loop behind a
  machine-local API. Reaches the hub outbound-only, so it keeps working while the hub is briefly unreachable — every
  such call carries the runner's enrolled bearer token (see "Runner authentication" below).

Each daemon owns its own embedded store; neither opens the other's.

## Install

Install the wheel into a self-contained, node-free virtualenv, seed each daemon's runtime directory once, drop the
units, and enable them:

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
#    its work sources are declared in blizzard-hub.toml's [[work_source]] blocks
#    (init scaffolds a commented-out example — see "Configuring work sources"
#    below); the runner's workspace/harness bindings live in its own blizzard-runner.toml,
#    written by `init` and edited in place (no credentials).

# 5. Install and enable both units. `enable` is what starts them at boot; `--now`
#    starts them immediately too.
sudo cp packaging/systemd/blizzard-hub.service packaging/systemd/blizzard-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now blizzard-hub.service blizzard-runner.service
```

If the wheel is installed somewhere other than `/opt/blizzard/venv`, edit the `ExecStart`/`ExecStartPre` paths to match
`command -v blizzard-hub` — systemd requires an absolute path there.

**Reconcile the graphs after every deploy — `blizzard hub graph sync`.** Unlike the store, which self-heals on restart,
a wheel's **graph** changes are inert until they are minted: graphs live in the hub's store, the hub resolves a *minted*
graph per chunk, and it never re-reads the packaged YAML. So a deploy that ships a changed graph and stops at the
restart leaves every new chunk running the previous definition — with no error, log line, or status output saying so.

```bash
# after the hub is back up, and before you consider the deploy done
/opt/blizzard/venv/bin/blizzard hub graph sync
```

It compares each packaged graph's **fully inlined** definition against the newest mint of its name and mints only what
actually differs, so it is safe to run unconditionally: an unchanged wheel mints nothing and churns no lineage. Inlined
is the operative word — a mint folds every file a graph references into the stored definition, so an edit confined to
one of those files, never touching `graph.yaml` at all, is a real graph change that a `graph.yaml` diff would miss
entirely. Which keys carry a file reference is enumerated by `blizzard hub graph mint --help`; diff all of them before
you call a deploy complete. A graph that fails to load or validate is reported as `failed` and exits non-zero without
stopping the others from reconciling.

Minting is **additive**: the new definition becomes `effective` and supersedes the prior one for future resolution,
while every in-flight chunk stays pinned to the definition it started under (moving one deliberately is
`hub chunk migrate`, below). Confirm with `blizzard hub graph list` — the newest per name should read `effective`.

**Upgrades self-heal the store — for an additive or backfill revision.** To adopt a new wheel, `pip install` it into the
venv and `systemctl restart` the units — no manual migration step. Each unit's `ExecStartPre` runs `… migrate` before
the daemon opens its store, so a wheel that ships a new schema revision reconciles the on-disk store to head on the next
start; the daemon refuses to start on a revision mismatch, so a forgotten migration fails loudly rather than corrupting
state. A graceful `systemctl
restart` also preserves in-flight work across the upgrade — see the recovery contract
below. That loud-failure guarantee is the whole safety story for a revision whose `upgrade()` only adds or backfills; it
is not for a **destructive** one, whose `upgrade()` deletes rows outright — see "The pr-opened-idempotent upgrade note"
below for the one revision so far that does. It also does not cover a **config** change the new wheel requires:
`migrate` reads `blizzard-hub.toml` before it touches the store, so a config the new wheel rejects fails `ExecStartPre`
and the unit never starts. One such rename ships today — see "The work-source key rename" immediately below, and make
that edit *before* the restart.

### The work-source key rename

**A hub whose `blizzard-hub.toml` still declares `[[pm_source]]` will not start on this wheel.** The key is now
`[[work_source]]`; the block's contents are unchanged. Rename every occurrence:

```toml
# before                # after
[[pm_source]]           [[work_source]]
```

The failure is deliberate rather than a silent alias: a `[[pm_source]]` block on a wheel that no longer knows the key
would parse as *zero* configured work sources — a hub that boots clean while every `work-items` read 503s and every
board label renders null. So `HubConfig.load` raises instead, naming the new key.

Two consequences worth planning around:

- **It fails at `migrate`, not at first use.** `blizzard hub migrate` and `blizzard hub
  host` both load the config, so
  under the systemd layout above the unit's `ExecStartPre=… migrate` is what fails and the daemon never comes up. Edit
  the toml in the same maintenance window as the wheel, before `systemctl restart`.
- **`token_env` is yours and needs no change.** It names an environment variable you chose; only the *table key* was
  renamed. A `token_env = "BZ_PM_TOKEN"` that works today keeps working — the scaffold's example value changed, your
  value need not.

**If you script against the API**, two related changes ride along:

- `GET /chunks/{id}/pm-items` still works on both daemons as a **deprecated alias** for `/work-items` (marked deprecated
  in the OpenAPI spec). Move to `/work-items`; the alias is a courtesy for out-of-tree callers, not a supported path
  forever.
- **Response bodies carry no alias.** The field `pm_pointers` is now `work_refs` on every chunk, queue, and envelope
  view. A client reading the old name gets an empty list, not an error — so this is the part that needs a code change,
  whichever path you call.

### The pr-opened-idempotent upgrade note

**`20260716_2206_hub_pr_opened_idempotent` is the first migration in either store whose `upgrade()` deletes rows** (the
escalation-takeover and graph-node-produces-checks revisions are the only other destructive revisions in either tree,
and both only drop columns). Closing a coordinator read-then-write race (issue #10) with a unique constraint on
`(chunk_id, repo)` first requires a store carrying the race's duplicate rows to no longer carry them, so `upgrade()`
deletes every `delivery_pr_opened` row but the earliest per `(chunk_id, repo)` before adding the constraint.
`downgrade()` only drops the constraint back — it does not restore the deleted rows; they are gone for good.

In practice this only ever removes true duplicates (a redundant `pr.opened` fact for a PR the forge had already
deduplicated to one), so no chunk loses a fact a human or the board ever relied on distinguishing. But because the
delete is unconditional and irreversible, **copy the hub's store file before restarting into a wheel carrying this
migration** — `cp <hub-dir>/data/hub.db <hub-dir>/data/hub.db.pre-pr-opened-idempotent` for the sqlite default, or the
equivalent for a configured postgres `db_url` (`bzh:sql-portable`) — the same caution any one-way migration deserves,
and not something `migrate`'s revision-mismatch guard can catch after the fact, since the delete is exactly what
reaching that revision means.

## Naming the runtime directory

Every verb that takes a runtime dir resolves it from three rungs, highest to lowest: the explicit flag or argument, then
an environment variable, then the current working directory. `init` and `host` accept a positional `DIRECTORY` as well
as `--dir`; passing both requires they agree, and a genuine command-line conflict exits non-zero naming both values.
`migrate`, `runner tick`, `runner pause`, and `runner start` take `--dir` only.

| Daemon | Variable        | Names                                                                              |
| ------ | --------------- | ---------------------------------------------------------------------------------- |
| hub    | `BZ_HUB_DIR`    | the hub runtime dir (`blizzard-hub.toml` + `data/hub.db`)                          |
| runner | `BZ_RUNNER_DIR` | the runner runtime dir (`blizzard-runner.toml` + `data/runner.db` + `runner.sock`) |

The units above pass `--dir` explicitly, so they are unaffected. The variable is for callers that cannot hand-write a
flag at every invocation — an operator shell aimed at a deployment, or winter's per-env band pointing one feature env at
a store snapshot or at a shared runtime dir during an exclusive handoff.

> **Selectable is not shareable.** The store is single-writer, and each daemon migrates on boot. Aiming a second live
> daemon at a runtime dir a running instance already holds risks lock contention and corruption — this variable chooses
> a root, it does not make one safe to share.

### Overriding config values from the environment

A container image cannot reasonably bake a `blizzard-hub.toml` per deployment, so the hub's deployment-varying config
values — the store URL, the bind host and port — also resolve from the environment, at load time. Precedence, highest to
lowest: **CLI flag > environment variable > toml value > built-in default**. `blizzard hub host` and
`blizzard hub migrate` resolve identically, since both read through `HubConfig.load`.

| Value    | Variable        | CLI flag                   |
| -------- | --------------- | -------------------------- |
| `db_url` | `BZ_HUB_DB_URL` | *(none)*                   |
| `host`   | `BZ_HUB_HOST`   | `--host` (`hub host` only) |
| `port`   | `BZ_HUB_PORT`   | `--port` (`hub host` only) |

Every variable unset leaves a deployment's resolved config byte-identical to a toml-only load. A malformed `BZ_HUB_PORT`
fails with a `ConfigError` naming the variable — from both `blizzard hub init` (which scaffolds a config on a fresh
runtime dir) and every later `load`, so a container's very first boot fails the same named way as any later one.

## Configuring work sources

The hub's work-item pass-through reads every chunk's work item through a **configured work source** — a named,
credentialed binding to one forge repo, declared as an `[[work_source]]` table in `blizzard-hub.toml`. This is a
separate seam from the delivery forge above: `BZ_FORGE_URL`/`BZ_FORGE_TOKEN` in the hub's env file control where a
chunk's PR is opened and landed; `[[work_source]]` controls where its work item is *read from*, and each source carries
its own credential rather than sharing the delivery forge's.

`blizzard hub init` scaffolds a commented-out example block — uncomment it and fill in your own repo to configure a
source:

```toml
[[work_source]]
name = "blizzard"                                  # source id — ingest tokens and board labels key on it
provider = "github"                                # the only adapter grammar today
repo = "paul-gross/blizzard"                       # the "owner/repo" this source is pinned to
token_env = "BZ_WORK_SOURCE_TOKEN"                          # names an env var — see credentials below
annotate = false                                   # opt into the forge-status label sweep — see below
close = false                                      # opt into the delivery closure sweep — see below
# api_base = "https://ghe.example.internal/api/v3" # optional: override the provider's API origin
# web_base = "https://ghe.example.internal"         # optional: override the web origin
```

Every field:

| Field       | Required            | Meaning                                                                                                                                                                                                                                           |
| ----------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`      | yes                 | The source's identity. Ingest tokens (`name:ref`, `name#ref`) and board pointer labels (`{source}#{ref}`) key on it. Must not contain `:` (the ingest token grammar splits on the first one). Must be unique across all `[[work_source]]` blocks. |
| `provider`  | yes                 | The adapter grammar this source speaks. Only `"github"` exists today; an unknown provider fails at config load, not at first use.                                                                                                                 |
| `repo`      | yes                 | The `owner/name` coordinate this source is pinned to. Each `(provider, repo)` pair may appear under only one `name` — two names for the same repo would let one item be ingested twice under two identities.                                      |
| `token_env` | yes                 | Names an environment variable — **not the secret itself**. See "Credential indirection" below.                                                                                                                                                    |
| `annotate`  | no, default `false` | Opts this source into the forge-status label sweep. See "The forge-status label projection" below — **do not set this on more than one hub against the same forge repo.**                                                                         |
| `close`     | no, default `false` | Opts this source into the delivery closure sweep. See "Closing delivered work items" below — **do not set this on more than one hub against the same forge repo.**                                                                                |
| `api_base`  | no                  | Overrides the provider's default API origin. Required to reach a self-hosted forge (e.g. GitHub Enterprise).                                                                                                                                      |
| `web_base`  | no                  | Overrides the provider's default web origin, used for the item's browsable URL. Derived from `api_base` when omitted, so a self-hosted GHE source only needs to set `api_base`.                                                                   |

**A self-hosted GitHub Enterprise example** — an internal repo behind a company GHE instance, alongside the public
`blizzard` source:

```toml
[[work_source]]
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

`name = "internal"` is a free choice **only** because `acme/internal-tool` is a brand-new source with no chunks minted
against it yet. That freedom does not extend to a repo that already has chunks in this hub — see the repo-tail rule in
the upgrade note below, which this example is not an illustration of.

### Credential indirection

`token_env` names an environment variable; the secret itself goes in the hub's env file (`/etc/blizzard/hub.env` under
the systemd layout above), never in `blizzard-hub.toml` — the same separation the delivery forge's `BZ_FORGE_TOKEN`
already follows. An unset `token_env` fails at boot, naming the missing variable rather than silently ingesting
unauthenticated.

### The forge-status label projection

Per source with `annotate = true`, the hub runs a periodic background sweep that projects every live chunk's status onto
its forge issue as one of two labels:

- `blizzard:ingested` — the chunk is minted but not yet claimed (`not_ready`/`ready`).
- `blizzard:in-progress` — a runner or the hub is actively working it (`running`, `paused`, `waiting_on_human`,
  `needs_human`, `delivering`).

A chunk with no live holder, or one that has reached `stopped`/`done`, carries neither label. The sweep runs every
`annotation_interval_seconds` seconds — a top-level `blizzard-hub.toml` key, default `120`, consulted only when at least
one source opts in — and holds **no state of its own**: each pass discovers the forge's actual labels afresh (listing
issues per label, not reading back what a prior sweep wrote) and writes only the difference from desired state. That
statelessness is what makes the projection self-healing: a label removed by hand, a crash mid-sweep, or a forge outage
all resolve themselves on the next pass, with no hub-side record to repair. A forge that is down, slow, or rate-limiting
degrades to a logged skip — it never blocks a chunk transition, an ingest, or any other hub request.

**Set `annotate = true` on at most one hub per forge repo.** Two writers sweeping the same repo will fight over the same
labels — each pass "correcting" what the other just wrote — with no coordination between them. Only the canonical
instance for a given forge repo should opt in; every dev/staging/snapshot hub pointed at that same repo must leave it
`false`.

### Closing delivered work items

Per source with `close = true`, the hub runs a periodic background sweep that closes every landed, non-grouped chunk's
still-open work refs through that source's own binding — the guarantee half of closing delivered work (issue #216); a
worker's own commit metadata, when the source honors it, is only an opportunistic hint that may beat this sweep to it on
a fast-forward landing.

Closing is **best effort and non-atomic**: each ref is attempted independently, one ref's failure never blocks
another's, and a failed attempt is retried on the sweep's next pass — there is no bound on how many passes a transient
forge outage costs, only that it eventually converges. Each ref's outcome (`closed`, `gone`, or `failed`) is recorded as
a durable fact and, the first time that outcome is recorded, one chunk-visible event (`work-item-closed` at `info`, or
`work-item-close-failed` at `warning` — covering both a retried `failed` attempt and a terminal `gone` one). The same
`annotation_interval_seconds` paces this sweep too — there is no second interval knob to configure.

**Set `close = true` on at most one hub per forge repo**, for the same reason as `annotate` above: two writers issuing
the same closing `PATCH` race each other with no coordination between them. A `STOPPED` chunk that never landed closes
nothing; a chunk that landed and was *later* stopped still closes — landing, not chunk status, is what this sweep gates
on.

### The upgrade note

**An existing hub must add at least one `[[work_source]]` block, or two things break on the next deploy:**

- `GET /chunks/{id}/work-items` 503s outright — "no work source is configured" — until at least one source exists.
- Every chunk's board pointer label goes null: rendering `{source}#{ref}` needs a source name, and there is none to
  render until a source is configured.

This is not optional for a hub that already ingests work items; there is no backward-compatible default, because the
work source list also bounds which repos the hub is willing to ingest from (see below). Add the `[[work_source]]` block
to `blizzard-hub.toml` as part of the same maintenance window as the wheel upgrade, before running `migrate`/restarting
the daemon (see the install/upgrade steps above).

**For a repo that already has chunks in this hub, `name` is not a free choice — it must be the repo's own tail** (the
part after the last `/`; e.g. `blizzard` for `paul-gross/blizzard`). An earlier release's migration
(`20260716_1512_hub_pm_pointer_source_ref`, which predates the `[[work_source]]` key and ran under its old name)
backfilled every existing pointer's `source` to its repo tail, so a `name` that does not match strands those pointers:
nothing 503s (the hub sees a non-empty source list and boots clean), but every pre-existing chunk for that repo silently
degrades — `label` goes `null` and its `work-items` entry carries
`error="no configured work source named '<repo-tail>'"`, because the pointer's `source` and the configured `name` no
longer agree. A repo with no chunks minted against it yet has no such constraint — any `name` is safe (the GHE example
above is exactly that case, not an illustration of the repo-tail rule).

**Verify you got it right** after the upgrade: for any chunk that existed before this release, read its work items and
confirm no entry carries an `error`:

```text
curl -s http://<hub>/api/chunks/<chunk_id>/work-items | jq '.items[].error'
```

Every value printed should be `null`. A non-null `error` naming a work source means the configured `name` does not match
the backfilled repo tail for that chunk's pointer — fix the `name` (or add a second `[[work_source]]` under the correct
tail) and restart.

### Ingest tokens

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk. Each token is one of:

- `<source>:<ref>` — e.g. `blizzard:26`
- `<source>#<ref>` — e.g. `blizzard#26`
- a pasted work item URL (e.g. the GitHub issue's own URL)

For the `github` provider, `<ref>` must be numeric (the issue number) — a `<source>:<ref>` or `<source>#<ref>` token
with a non-numeric `ref` (e.g. `blizzard:v2`) matches no configured source's `parse` and surfaces as the same 422 an
unconfigured repo gets ("not claimed by any configured work source"), which misdiagnoses as a missing `[[work_source]]`
rather than a malformed ref.

The CLI carries no parsing of its own: it hands the token to the hub, which resolves it against every configured
source's own `parse`. The legacy `github:<rest>` prefix is deprecated — it still resolves (warns on stderr, then passes
`rest` on its own merits) but carries no provider selection of its own anymore, since a token now resolves against
whichever configured source claims it.

### Unconfigured repos are a 422 at the front door

The configured source list is also the hub's allowlist of ingestable repos: a token that names a repo (via URL or an
unresolvable source name) that no `[[work_source]]` covers gets rejected with `422 Unprocessable Entity`, naming the
token and the sources that *are* configured. Adding a repo to the fleet means adding its `[[work_source]]` block first —
there is no separate allowlist to keep in sync.

## Runner authentication

This is **machine identity** — a runner authenticating itself to the hub — distinct from the **human login** plane
("Human authentication (OAuth login)" below), which authenticates an operator to the hub's own web/API surface.

Two independent rollout flags gate the fleet's runner-identity and route-capability defenses, both scaffolded into
`blizzard-hub.toml` by `blizzard hub init`, both defaulting to `warn`:

| Flag               | Guards                                                                           | `warn` (default)                                                  | `enforce`                        |
| ------------------ | -------------------------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------- |
| `runner_auth_mode` | every fleet-router call's bearer token resolves to a known runner identity       | logs a missing/invalid/mismatched token and lets the call proceed | rejects it (401/403)             |
| `route_token_mode` | the per-acquisition route capability token presented on every chunk-scoped write | logs a missing/mismatched route token and lets the write proceed  | rejects it as a semantic failure |

They are independent on purpose — a fleet can flip one on before the other — and neither has any effect while `warn`; a
fresh deploy or an upgraded hub keeps working unauthenticated until an operator deliberately tightens them.

**One route ignores `runner_auth_mode` outright.** A runner reading back its own shipped transcript segments
(`GET /api/fleet/chunks/{chunk_id}/transcript-segments`) is gated on that route's own always-raising ownership check
instead: it refuses (401/403) a caller whose token doesn't resolve or names a different runner than the segments' owner,
regardless of the flag's `warn`/`enforce` setting — unlike the rest of the fleet router, where `warn` leaves an
unresolved or mismatched token to proceed.

**Enrollment requires the runner to have registered first.** A runner registers itself with the hub on its own pull;
`blizzard hub runner enroll <runner_id>` 404s naming the unknown id until that has happened at least once. Enrollment is
a deliberate operator act on a runner the fleet already knows, not a trust-on-first-use grant to a name nobody has
registered yet.

The rollout sequence, in order:

1. Start the runner once so it registers with the hub.
2. `blizzard hub runner enroll <runner_id>` — mints (or, run again, rotates) the runner's bearer token and prints the
   plaintext exactly once; there is no way to read it back later, only to rotate it.
3. Install that token in the runner's own runtime env file (the systemd `EnvironmentFile`, or the shell env a
   manually-run runner inherits) under the variable its `token_env` config key names — see "The runner's outbound token"
   below.
4. Flip `runner_auth_mode` to `enforce` in `blizzard-hub.toml` and restart the hub, once every runner in the fleet
   carries an enrolled token.
5. Flip `route_token_mode` to `enforce` only after outbound buffers carrying pre-upgrade, token-less facts have drained
   — `warn` already covers that window, so there is no separate grace period to wait out beyond it.

### The runner's outbound token

`blizzard-runner.toml`'s `token_env` (default `BZ_HUB_TOKEN`) names the environment variable carrying the runner's
enrolled bearer token — never the secret itself, mirroring the `[[work_source]] token_env` indirection above. The secret
goes in the runner's runtime env file (e.g. `/etc/blizzard/runner.env` under the systemd layout, declared as that unit's
`EnvironmentFile`), read once at config load. Every outbound runner→hub call — the reconciliation loop's `httpx.Client`
and the work-items proxy alike — attaches it as `Authorization: Bearer <token>`; an unenrolled runner (or one whose env
file has not been updated yet) attaches nothing, and `runner_auth_mode` above decides whether the hub tolerates that.

### Forwarding extra vars to workers

`blizzard-runner.toml`'s `[worker] env_passthrough` is the operator's lever to widen the fixed base allowlist
(`PATH`/`HOME`/`USER`/`LANG`/`LC_*`/`TERM`/`TMPDIR`) every worker/judge/resume child process is built from — name a
variable there to forward it into every spawn too. Empty (the fresh-scaffold default) means the base allowlist only; a
daemon credential such as `BZ_HUB_TOKEN` is never in scope for this list, so it is absent from a worker child by
construction unless deliberately named here.

**One child is built the other way around: an operator takeover.** A session continued via `blizzard runner takeover`
runs in *your terminal's* environment — your shell as the base — with only a bounded daemon-side set layered on top: the
lease's `BLIZZARD_*` identity vars plus the daemon's `PATH` and `HOME`. Nothing named in `env_passthrough` is forwarded
to it, and no allowlist filtering applies to your own shell. See "Taking over a parked session" under the control verbs.

### Model and effort tiers

A graph's `sessions:` map names each session lineage's **capability tier** rather than a model — `blizzard:frontier`,
`blizzard:advanced`, `blizzard:basic` — and a chunk's `default_model` uses the same vocabulary. The hub never interprets
either: the mapping from a tier to a model *this* runner's harness understands lives in `blizzard-runner.toml`, which is
what keeps a graph harness-agnostic. A runner on a second harness would map the same three tiers to that harness's own
models and skip `opus` wherever a preference list names it — Claude Code is the only adapter that ships today.

```toml
[models.aliases]
"blizzard:advanced" = "claude-opus-5"
"blizzard:basic" = "haiku"

[effort.aliases]
max = "xhigh"
```

Both tables are optional. The Claude Code adapter ships built-in defaults for the three standard tiers (frontier →
`fable`, advanced → `opus`, basic → `sonnet`), so a zero-config runner resolves them with no `[models.aliases]` at all;
an entry here overrides the built-in for that alias. `[effort.aliases]` maps onto the well-known `low|medium|high|max`
ordinal — the four need no entry, and the table exists so a deployment can name its own vocabulary or reach a native
tier outside the ordinal (Claude Code's own `xhigh`).

A `model` preference list resolves **left to right**: the first entry this runner can resolve wins, and an entry it
cannot — an unmapped alias, or a name belonging to another harness — is **skipped**, never a spawn failure. A list
nothing in resolves falls back to the runner's own default model with a logged note naming what it skipped. The aliases
are deliberately **unordered roles, not a scale**: nothing substitutes downward when a tier is unmapped, so every
degradation is something a graph author wrote.

#### Session stickiness — a deployment requirement

A session's **model** is applied when the session is minted and on no resume after it. That rests on the harness
restoring a resumed session's own model, which all three target harnesses do — and which each one has a configuration
that **defeats**. A deployment that trips one runs its mechanical lineage on the wrong model with every test tier still
green, so these are requirements, not preferences.

Only the first binds a deployment you can run today; Claude Code is the one adapter that ships, and the other two are
the obligation an adapter for that harness would inherit:

- **Claude Code** — a worker must never see the `ANTHROPIC_MODEL` family of variables. They are absent from the base
  allowlist by construction; do not add one through `[worker] env_passthrough`. The by-construction guarantee covers
  **daemon-spawned** children only: a `blizzard runner takeover` session inherits your shell, so a shell that exports
  `ANTHROPIC_MODEL` moves that one session off its sticky model — unset it before taking over.
- **opencode** — an adapter must not pin `agent.<name>.model`; it outranks session stickiness.
- **codex** — an adapter must keep `model` out of `config.toml` (it overrides every resume), and needs a state-DB-era
  codex to restore a thread's model at all.

**Effort is different, and is reasserted on every invocation.** Claude Code does *not* restore a session's effort across
`--resume`: a session spawned at one effort reverts to the settings-resolved default on the next resume (measured
against CLI 2.1.220). Applying it at mint only would therefore silently drop a declared effort on every member of a
resuming pool, so the runner passes `--effort` on each turn. The cost is small and measured — 249 cache-creation tokens
against 17 for a bare resume, nothing like the full-history rewrite a cross-model resume forces.

**A declared compaction window is treated the same way as effort (blizzard#343): reasserted, never mint-only.** A
`sessions:` entry can carry a fourth, optional facet — a compaction window, an opaque string passed straight through to
Claude Code's `--autocompact <auto|tokens>` flag on every fleet-driven invocation (spawn, judge, resume-with-message).
Whether the harness restores a resumed session's own window is unmeasured, so the runner does not bet on stickiness the
way it does for `model` — it stamps the resolved window on the lease at mint and reasserts it from that stamp on every
resume, exactly as it does for effort. An unrecognized or empty value is dropped with one log line rather than failing a
spawn. `advanced-development-workflow` declares one on all four of its pools at the same value, above the
`rotate.max_context_tokens` its bounded pools carry (the only one of those three rotation bounds a window is
commensurable with). The ordering is the whole decision: set below that bound, a window fires first and keeps firing
inside one long node, costing the worker its working context on every firing; set above it, rotation ends an ordinary
lineage first and the window is left as a ceiling on the one invocation that outgrows it before the next resume is
measured. Neither number is fit from measured compaction behavior the way the rotation bounds are, since no such data
exists yet.

### The worker spawn preamble

A worker's **first** spawn on a session carries three ordered layers ahead of the node's own envelope prompt: (1) a
baked-in blizzard preamble — framing the worker as operating inside the fleet, naming its worker-facing
`blizzard runner` verbs (`ask`, `work-items`), and stating the turn-ending discipline a headless session is held to
(nothing survives the turn that started it) — (2) the operator's own `workspace_prompt` prose, layered on top when set,
and (3) a machine-local facts table (runner/chunk/lease identity, held environment(s)). Layer 1 closes by stating that
division of labor for the worker's own benefit; read the shipped text
(`src/blizzard/runner/harness/prompts/blizzard_preamble.md`) before authoring layer 2, so your prose adds
deployment-specific policy rather than re-establishing framing the worker already has.

#### Adopting a packaged sample instead of authoring layer 2

Blizzard ships a corpus of workspace prompts — one per deployment shape — so a workspace whose shape is already
represented names one rather than writing layer 2 from scratch. `blizzard runner prompt list` names what this wheel
carries, and `blizzard runner prompt show <name>` prints one. A sample is never applied by default; it takes a knob:

```toml
workspace_prompt_package = "winter"
```

That knob resolves the named sample out of the installed wheel at `host` startup, so nothing lands in the runtime root
and a redeploy carrying a changed sample applies it on the next restart. It is **exclusive** with `workspace_prompt` and
`workspace_prompt_file` — those two keep their own file-wins-over-inline precedence, and setting the package knob
alongside either fails startup rather than ranking them. A name the corpus does not carry fails startup too, listing
what it does carry.

To fork a sample instead of tracking it, `blizzard runner prompt install <name>` copies it into the runtime root and
sets `workspace_prompt_file` at the copy — never the package knob, so `blizzard runner prompt diff <name>` always has a
local file to compare and can report drift from the sample it came from. `blizzard runner prompt status` reports which
source the effective prompt resolves from, and exits non-zero when a source is configured but resolves to nothing.

#### What the packaged graphs delegate to layer 2

`workspace_prompt` is unset by default, and the packaged graphs still work without it — their prompts state each duty as
an outcome a worker can satisfy on its own. But two of those duties are ones a workspace usually has a specific, better
answer for, and the prompts defer to it by name ("if this workspace declares one, prefer that"). Authoring layer 2 is
how you supply that answer:

- **Getting onto the feature branch.** The build prompts require that no push from a leased environment can reach the
  base branch, and leave *how* to the workspace. If your workspace has a command that points every repo's upstream at
  the feature branch in one step, name it — a worker doing this per-repo by hand is the slower, more error-prone path,
  not a different outcome.
- **Where scratch files go.** The prompts require drafts to land outside every repository working tree and outside the
  workspace directory the worker was spawned in, and fall back to a per-chunk directory in the machine's temporary
  space. If your workspace owns a scratch area that something actually sweeps, name it.

Neither is a safety gap when layer 2 is absent — the prompts are self-sufficient — but a deployment that has better
answers and does not state them leaves workers taking the generic path.

#### What a resumed spawn gets instead

Layers 1 and 2 are *standing* prose — a session that already received them still holds them. So on a node-step that
**resumes** an existing session, the runner sends each of those two layers only when it has actually changed since that
session was last spawned:

- **Unchanged** — the layer collapses to a single line stating that it still applies. On a graph like
  `advanced-development-workflow`, whose worker nodes resume by default (`plan`, `build`, `verify`, `pre-push`,
  `resolve`, `retrospective` — only `plan-review` and `review` are declared `fresh`), that is the ordinary case at every
  one of them.
- **Changed** — the new prose is sent in full, led by an explicit statement that the worker's standing instructions have
  been updated since its previous turn. A workspace prompt replaced with an empty one is a change too, and is announced
  as a withdrawal.

That announcement is the operator-visible reason `PUT /api/workspace-prompt` is trustworthy mid-chunk: a replace applies
to the chunk's next resumed node-step, and the worker is told it is reading something new rather than being handed
replacement prose in the same position the superseded block occupied. `runner_prompt` behaves the same way once it
moves, but it is a startup knob — reaching a running fleet still takes the restart the section below describes.

An override is a standing one: it wins over every config knob until it is removed, and replacing it with empty text sets
a standing *empty* prompt rather than restoring the configured one. `DELETE /api/workspace-prompt` is the way back — it
drops the override so the config resolves again, which is what makes the override usable as a live scratchpad for prose
you intend to land in the corpus.

**Layer 3 is unconditional on every path.** The facts table is re-rendered per attempt around a freshly minted
`lease_id`, and a worker whose table named a dead lease could not address the fleet at all. A fresh spawn, and any node
declared `session: fresh`, renders all three layers exactly as before.

Layer 1 is overridable but never *unset* — some layer-1 prose is always in effect, even on a resumed spawn that only
restates it in one line. `blizzard-runner.toml`'s `runner_prompt` (inline text) or `runner_prompt_file` (a path, wins
over inline text when both are set) — or `BZ_RUNNER_PROMPT` seeding a fresh scaffold — replaces the baked default
wholesale when set; unset, the baked default renders. Both are config/startup knobs, resolved once at `host` startup —
unlike `workspace_prompt`, which also has a live `PUT /api/workspace-prompt` override, `runner_prompt` has no runtime
door, so changing it means restarting the runner. A `runner_prompt_file` naming a path that does not exist raises a
`ConfigError` at startup, the same fail-fast the workspace-prompt file knob already gives.

## Human authentication (OAuth login)

Distinct from "Runner authentication" above: this plane authenticates an **operator** logging into the hub's own web/API
surface, not a runner authenticating itself to the hub. The hub's `[auth]` table (scaffolded into `blizzard-hub.toml` by
`blizzard hub
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

`mode = "none"` (the shipped default) resolves every request to the implicit operator/superuser identity with no store
read — a fresh or upgraded hub keeps working unauthenticated until an operator deliberately opts in. `mode = "oauth"`
activates the session/permission seam and requires at least one `[[auth.oauth.provider]]` entry. `type` selects the
conformer: `"github"` (an OAuth App) or `"oidc"` (a generic OIDC issuer, discovered via
`<issuer>/.well-known/openid-configuration`). `client_secret_env` mirrors `[[work_source]] token_env`'s indirection
exactly — it names an environment variable, never the secret itself; the secret goes in the hub's runtime env file (e.g.
`/etc/blizzard/hub.env` under the systemd layout above), a deployment credential like
`BZ_FORGE_TOKEN`/`BZ_WORK_SOURCE_TOKEN` above.

### The superuser bootstrap

`[auth].superuser` names one email as the fleet's bootstrap identity, ensured at every hub boot: once a verified login
matches that email, the hub promotes that user to `superuser`; until then, the intent is pre-provisioned and unclaimed,
and the boot log (plus an `auth_facts` entry) surfaces that on every restart rather than failing silently. Changing
`superuser` to a different email demotes whichever user the previous target had claimed back to `admin` — at most one
user is ever the bootstrapped superuser at a time, and this is the *only* way a user becomes (or stops being)
`superuser`; the role is never assignable through the admin API.

### Roles, in one paragraph

A hub-local user carries one of five roles, a total order — `pending < guest < contributor < admin < superuser`. A
freshly-logged-in identity lands as `pending`: the lobby, holding no permissions at all beyond the public self routes
(`GET /api/me`, login, logout) — no board read, no writes. `guest` reads the fleet's state (the board, chunks, graphs,
events) and mutates nothing, but not a chunk's stored transcript segments — an operator's read of those needs
`contributor`+ (`transcript:read`) on this role ladder, since a transcript carries everything a worker saw. A second
reader sits outside this ladder entirely and outside this table: a runner reading back its own shipped segments, gated
on a runner bearer token rather than a hub-local role — see "Runner authentication" above. An `admin` (promoted from the
admin page, `POST /api/users/{id}/role`, gated on `user:manage`) can move a subject freely among
`pending`/`guest`/`contributor`, but only a `superuser` actor may grant or revoke `admin` itself, and `superuser` is
never assignable through that API in either direction — it is bootstrap-only, per the previous section.

### The hub board's Transcripts tab

The hub's chunk board page (`/board/chunk/:chunkId`) carries a Transcripts tab last in its four-tab strip — General,
Node history, Artifacts, Transcripts — gated on `transcript:read` the same way the API route above is: an operator
without it never sees the tab option, and a held deep link to one renders an honest permission notice rather than a
generic error. Open, it lists the chunk's node-history steps, each holding the transcript segments a runner shipped
while working that step; opening a segment fetches its turns lazily, including any nested subagent conversation and the
harness's own private reasoning, and a step whose one attempt spans multiple segments (a resumed session within the same
node and epoch) links them end to end so that attempt's whole conversation reads in order. A bounce back into an earlier
node (a build that failed review and ran again) is a **later epoch** — its own step, never stitched to the attempt
before it.

The runner's own machine panel serves the same route on its own host, with its own three-tab strip — General, Artifacts,
Transcripts; it carries no Node history tab of its own — a separate, runner-local surface under its own authorization,
not this hub-scoped gating; see "Runner authentication" and "The runner's two doors" above.

### Operator verbs

`blizzard hub login` logs an operator into the hub: by default it opens a browser to the hub's own authorize endpoint
(PKCE, an ephemeral `127.0.0.1` loopback redirect) — the user completes login *at the hub*, and the resulting session
token is stored locally. `--paste` swaps that for the paste-code fallback (the hub renders a short one-time code the
user pastes back into the prompt), for a headless/remote shell with no reachable loopback listener.
`blizzard hub logout` deletes the locally stored session and revokes it at the hub, so it stops resolving even if it
leaked. `blizzard hub rotate-signing-key` rotates the hub's IdP signing keypair — mints a fresh current key, demoting
the old current to previous; runners pick up the new key by re-fetching JWKS on an unknown `kid`, no restart needed.
Under `mode = "oauth"`, `rotate-signing-key` is itself gated on `user:manage` and requires a logged-in session.

### Runner-side federation

A runner that wants its own human web surface reachable via the hub's SSO bounce declares `public_url` in
`blizzard-runner.toml` — the browser-reachable base URL(s) it answers on, from which the runner derives the redirect
URIs it presents to the hub's IdP authorize endpoint (`<public_url>/api/auth/callback` each). Empty (the fresh-scaffold
default) means this runner registers no federation identity, so its human web surface stays unreachable via SSO — and,
since there is no IdP to bounce to either way, that is also the correct state when the hub itself runs
`auth.mode = "none"`.

`public_url` takes **one URL or a list of them**. More than one matters because the hub delivers the federation token by
making the *browser* POST to the redirect URI, so a redirect URI is followed by the browser rather than the runner, and
so resolves in the network namespace of whichever device is holding it: a runner declaring only `http://127.0.0.1:8431`
is reachable from a browser on its own host and nowhere else, since any other device follows that address to itself.

Two constraints bound what is worth declaring. First, **only two origin classes can complete a bounce**: a loopback
origin at either scheme, and a non-loopback origin only as `https`. A non-loopback plain-`http` origin is not merely
insecure — the bounce cookies cannot be `Secure` there, browsers refuse `SameSite=None` without it, and the cross-site
callback arrives cookie-less, so `http://192.168.1.5:8431` fails every time it is tried. Second, **each entry must equal
the origin the browser shows, exactly** — scheme, host, and port — because selection compares it against the request's
`Host`. A proxy terminating TLS on 443 makes the browser-visible origin `https://runner.example`, which a declared
`https://runner.example:8431` does not match; the mismatch is silent, since the fallback lands on a registered origin
and the hub raises nothing.

`localhost` and `127.0.0.1` are also distinct origins to both the browser and the hub's exact-match guard, so each needs
its own entry. Two spellings that a browser cannot tell apart — differing only in scheme, or in an
explicit-versus-default port — are refused at load rather than silently resolving to whichever was declared first:

```toml
public_url = ["http://127.0.0.1:8431", "http://localhost:8431", "https://runner.example"]
```

Every declared origin is registered with the hub, which exact-matches the presented redirect URI against that registered
set. The runner then selects among the declared origins per request, by the arriving `Host`; a request whose `Host`
matches none falls back to the first declared origin, which is the canonical one the hub records as this runner's URL.
Selection is membership in the declared set and never construction from the request, so an unrecognized or forged `Host`
can only ever resolve to an origin the operator already declared — it is logged as a warning and falls back, never
reflected into a redirect URI. A value that is not a URL or a list of them, an entry carrying a path, userinfo, or a
port that is not a number, and two entries naming one browser origin all fail at config load rather than surfacing later
as an opaque `unregistered redirect_uri` refusal from the hub.

Registration happens on the runner's reconciliation tick, so a widened set reaches the hub on the next tick after a
restart — a login attempted between the restart and that tick is refused as an unregistered redirect URI. Any `https`
entry served through a TLS-terminating proxy also needs `trusted_proxies`, which is a hard requirement for that case
rather than a refinement, and needs the proxy to preserve the browser's `Host`; both are covered under
[Behind a TLS-terminating reverse proxy](#behind-a-tls-terminating-reverse-proxy) below.

Runner-local role resolution is a separate `[auth]` table, living only on the runner — never in the hub store or its
admin page:

```toml
[auth]
# superuser = "<hub-username>"   # this runner's own sovereign, config-only
hub_role_default = "mirror"      # "mirror" (reproduce the hub's own role claim) or a
                                  # fixed cap ("contributor"/"guest"/"pending")

[auth.users]
# ada = "admin"                  # per-hub-username role overrides
```

`superuser` names a hub **username** as this runner's own sovereign — never assignable through a JWT claim, a
config-only designation mirroring the hub's own `auth.superuser` bootstrap identity. `hub_role_default` is the fallback
runner-local role for a hub identity with no `[auth.users]` override: `"mirror"` (the default) trusts the hub's own
`role` claim verbatim, or a fixed cap (`"contributor"`/`"guest"`/`"pending"`) floors every unmatched identity regardless
of hub role. `[auth.users]` overrides that default per hub username, resolved from the JWT's `username` claim only
(never `email`, which is mutable and may be null).

### Behind a TLS-terminating reverse proxy

The two decisions this plane derives from the connection — the session cookie's `Secure` flag (from the request scheme)
and the login throttle / `auth_facts` actor IP (from the peer address) — are correct when a daemon is exposed
**directly** (localhost, tailnet) and wrong behind a TLS-terminating reverse proxy (nginx, Caddy, an ALB). The proxy
speaks HTTPS to the browser but plain HTTP to the daemon, so the daemon sees `http` (and mints the session cookie
*without* `Secure`, even though the deployment is HTTPS end to end), and every request arrives from the proxy's own IP
(so one noisy client collapses the whole fleet into a single throttle bucket).

For a **runner** whose `public_url` declares a proxied `https` origin, this is not a degradation but an outright failure
of the SSO bounce, so `trusted_proxies` is a hard requirement there. The hub returns the federation token by a
cross-site `form_post`, and the runner's bounce cookies only get `SameSite=None` (which browsers honor only alongside
`Secure`) on an origin it believes is secure. Reading the scheme as `http` drops them to `SameSite=Lax`, the browser
withholds them on that cross-site POST, and the callback fails its state check — surfacing as `bad or expired state`,
which names nothing about the cause. A **loopback** origin is exempt: browsers treat loopback as potentially trustworthy
whatever the scheme, which is why a `127.0.0.1` runner federates against a hosted hub with no proxy configuration at
all.

`trusted_proxies` — a top-level key in **both** `blizzard-hub.toml` and `blizzard-runner.toml` — lists the proxy
addresses or CIDRs whose forwarded headers are trusted:

```toml
# hub or runner runtime config
trusted_proxies = ["10.0.0.0/8", "192.168.1.7"]
```

When — and only when — the direct peer matches a listed proxy, `X-Forwarded-Proto` decides the effective scheme (the
cookie `Secure` flag, on both daemons) and the **rightmost untrusted hop** of `X-Forwarded-For` becomes the throttle /
fact client IP. A request from any other peer keeps its direct-connection values regardless of what headers it carries —
so a direct client cannot forge its scheme or spoof an `X-Forwarded-For` to dodge the throttle. Empty (the default)
ignores both headers from every peer, byte-identical to a direct-exposure deployment.

The proxy must set both headers, and in front of a **runner** it must additionally pass the browser's original `Host`
through unchanged — per-origin callback selection reads that header and nothing else, so a proxy that replaces it makes
every request look like it arrived on the proxy's own upstream address. nginx replaces it by default
(`Host: $proxy_host`), which is why it is set explicitly here:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
client_max_body_size               16m;
```

`client_max_body_size` is there for the transcript lane, not for headers: one shipped record may reach the hub's
per-record cap — 10 MB by default, and settable — while nginx's own default is **1 MB**, so a proxy left at that default
rejects a large record with a 413 before the hub ever adjudicates it. Raise it whenever the hub's `record_max_bytes`
rises. Caddy has no equivalent default limit.

Caddy's `reverse_proxy` and Tailscale's `tailscale serve` set all three headers automatically. Only `X-Forwarded-*` is
honored — `Forwarded` (RFC 7239) and proxy-protocol framing are not consulted, and no `X-Forwarded-Host` is read, so a
rewritten `Host` cannot be recovered.

A runner whose `Host` is rewritten fails the way an undeclared origin does: the bounce falls back to the canonical
origin, the hub accepts it as registered, and the browser is sent to whatever that origin names — its own machine, for a
loopback canonical. The runner logs a warning naming the arriving `Host` and the declared set when that fallback fires,
which is the signal to check this configuration.

## Produces-artifact enforcement

`produces_mode` is a third rollout flag, scaffolded into `blizzard-hub.toml` by `blizzard hub init` alongside
`runner_auth_mode`/`route_token_mode` above and defaulting to `warn` the same way — but it guards a different concern:
not runner identity or route capability, a node's own `produces:` declaration. Each `produces:` entry carries a
**kind**: a bare string (`review-findings`) is an `asset`; a `{name, kind: git_commit}` entry (a build node's own
commit) is met by kind, not by name — any `git_commit` artifact the node's attempt carries covers it. A `git_commit`
entry is met when the worker has **pushed** its branch to the forge and then **declared** that push — the worker pushes,
never the runner, and an undeclared push does not count. A name backed only by the worker's judgement-assessment
fallback is not proof the worker produced the thing the graph asked for.

| Flag            | Guards                                                                                                                    | `warn` (default)                                                                  | `enforce`                                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------- |
| `produces_mode` | every `produces:` entry has an explicit declaration matching its kind (an asset attachment by name, a git commit by kind) | logs the missing names and lets the completion proceed on the assessment fallback | rejects the completion as a semantic failure |

The worker declares each kind through its own `blizzard runner artifact` verb: `artifact
create --name <name>` (content
on stdin) for an `asset`; `artifact commit --repo <repo>
--branch <branch> --commit <sha>` for a `git_commit`, run after
the branch is pushed. The origin is not a flag on that verb: it comes from the environment's repo manifest. Both verbs
are pure clients of the runner's local API, authorized by the lease identity the runner injects at spawn — see
[`openapi/runner.openapi.json`](../openapi/runner.openapi.json) for the endpoints
(`POST /api/leases/{lease_id}/attachments` and `POST /api/leases/{lease_id}/git-commits`) rather than this doc
hard-copying their request shape.

It is independent of `runner_auth_mode`/`route_token_mode` — flipping it does not depend on either of them, and vice
versa — so it is not part of the rollout sequence above. A fresh deploy or an upgraded hub keeps accepting
assessment-fallback completions until an operator deliberately flips it to `enforce` in `blizzard-hub.toml` and restarts
the hub.

## Graph-scoped artifacts

Where `produces:` declares what a *node* must hand back, a graph's top-level `artifacts:` map declares content the
**graph itself** carries — reference material every node of every chunk on that graph can read, authored once beside the
definition. It is a sibling of `nodes:` and `sessions:`, and each value is a path to a file next to `graph.yaml`:

```yaml
artifacts:
  docket: ./docket.md
```

The file's text is folded into the definition at mint, the way a `prompt` reference is — see the `graph sync` paragraph
under "Install" above for what that means for a deploy, and `blizzard hub graph mint --help` for the inlining rules
themselves. A `prompt` and an `artifacts:` value are not folded by the same rule, though, and the difference decides how
you author: a `prompt` value is inlined only when it *reads* as a path, so literal prompt prose stays literal, while
**every** `artifacts:` value is read as a filename. In a `graph.yaml` loaded from disk, an artifact value carrying
inline text is therefore not accepted at all — the loader tries to open a file named by that text, and fails the load
naming the entry.

That leaves inline text authorable on exactly one path: a definition arriving with **no directory to resolve against**,
piped through `blizzard hub graph mint -` or posted straight to `POST /api/graphs`. Nothing is inlined there, so the
text has to arrive in the definition itself — and a value that still reads as a file path is rejected as a validation
error rather than baked in as the artifact's content. That guard fires on a single whitespace-free token carrying either
a `/` or a filename extension; real content is prose, which carries whitespace, so it cannot collide with it. One shape
slips through knowingly: a bare extension-less token like `notes` is as plausible a one-word artifact as it is a
filename, and mints as content.

And because the content is baked, editing the referenced file changes nothing for the chunks already running: they stay
on the mint they started under until a `graph sync` mints a new one.

Each name must be alphanumerics with internal `-`, `_`, or `.` separators — non-empty, no leading or trailing separator,
no two separators in a row (`a--b` and `a._b` are both rejected), and no `/`, since the name is percent-encoded into a
URL path segment on the way to a worker. A name that collides with any node's `produces:` name is rejected too: a worker
reaches both scopes through the one artifact CLI, so a shared name would be genuinely ambiguous rather than a legal
shadow.

**The graph scope is read-only to workers.** A worker reads an entry with
`blizzard runner artifact get <name> --scope graph` (add `--content` for the raw text), and `artifact list` includes the
graph's entries alongside the node's own. The writing verbs refuse the scope outright — `artifact create`,
`artifact commit`, and `artifact staged` all reject `--scope graph`, since a mint-time declaration is not something an
attempt produces.

**Authored order is fixed at the mint that first carried an entry.** Reconciliation mints only when a packaged graph's
parsed definition differs, and reordering two `artifacts:` entries without changing either name or either file is not
such a difference — `graph sync` reports the graph `up-to-date` while workers keep seeing the original order. The same
holds for `sessions:`. To move an entry, pair the reorder with a substantive edit to the graph or one of its referenced
files. To read back what a mint actually carries, `blizzard hub graph show <graph_id> --json` lists the artifact names
in their baked order — the default human rendering is nodes and edges only, so `--json` is the one that shows them.

## The runner's two doors

The runner daemon serves one API on two listeners, and which one you address depends on who you are:

| Client                                                                    | Door                                         | How it addresses it                                          |
| ------------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| the CLI's local verbs (`runner pause`, `runner start`, `runner takeover`) | `runner.sock`, mode 0600, in the runtime dir | `--dir` (or `$BZ_RUNNER_DIR`) — no port, no config file read |
| the runner's web app in a browser                                         | the TCP port (`8431` by default)             | same-origin `/api/*` on the page's own host                  |
| worker hooks (`heartbeat`, `ask`, …)                                      | the TCP port                                 | `BLIZZARD_RUNNER_URL`, injected into the spawn               |

Same app, same routes — two doors, not two APIs. A browser cannot open a unix socket, which is why the TCP listener
exists; the socket exists because the operator's controls should not depend on a port, and filesystem permissions are
their access control.

The TCP door also carries `GET /api/events/stream` (issue #317) — a `text/event-stream` route in the same human-facing
lane as the web app, deliberately absent from the OpenAPI spec since no generated client calls it. It publishes a lease,
ask, escalation, takeover, environment, or fact change the instant it happens, replaying from a `Last-Event-ID` on
reconnect exactly as the hub's own stream does; see "Operational visibility — the event log" below for the hub side of
the same mechanism. The runner's own web panel is its one subscriber (see below).

**Run the local verbs as the service account.** The socket is mode 0600 and the unit runs as `blizzard`, so the
filesystem access control above is doing its job: another account — including root's shell habits — is not the owner,
and the verb fails with `EACCES`. Use the same `sudo -u` form the install steps use:

```bash
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner pause --dir /var/lib/blizzard/runner
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner start --dir /var/lib/blizzard/runner
```

The board's copyable wrapped takeover command (issue #251; see "Taking over a parked session" below) supplies that
`--dir` for you, but nothing else here:

- **Not the service account** — the pasted command still needs a shell already set up exactly like the `sudo -u` form
  above, on the runner's own host.
- **Not the venv's `blizzard` binary path.**
- **Not the host itself** — `--dir` names a path on the **runner's** host, while the board is served by the **hub**, so
  on a split deployment ([`docs/remote-runner.md`](./remote-runner.md)) a pasted command can fail outright by landing on
  the wrong machine entirely, not just the wrong account or binary path.

`--runner-url` (or `$BZ_RUNNER_URL`) points a local verb at the TCP door instead — for a shell that cannot see the
runtime dir, or cannot open the socket. Passing both `--dir` and `--runner-url` explicitly is an error; an explicit flag
beats either variable, and if both arrive from the environment the socket wins (the default transport). Note the two are
different namespaces: `$BZ_RUNNER_URL` is this operator setting, while `BLIZZARD_RUNNER_URL` in the table above is
spawn-injected worker identity the runner mints per worker — setting one does not affect the other.

`runner pause` and `runner start` are pure clients of this API and never contact the hub, so they keep working while it
is unreachable. They set the runner's **own** brake, which means something different from
`blizzard hub runner pause <runner_id>`: the hub brake still just stops new claims (in-flight chunks always run on); the
runner's own brake means "start no processes on this machine" — no new claims, but also no restart-resume, no requeue
respawn, and no judging a worker that exits while it's on, since judging one resumes its session. Nothing is lost either
way: a live worker already running is left alone (this is not a drain), and every lease, route, and retry budget the
brake defers is picked up once it clears — exactly where it left off, unless an operator moved the chunk meanwhile — see
`blizzard-runner pause --help` for the full contract. Each brake is cleared only where it was set — `runner start`
locally, `blizzard hub runner resume` at the hub.

The panel's leases, environments, asks, escalations, takeovers, and facts render live: new events fan out over the
runner's own SSE spine (`/api/events/stream`, issue #317), so an open panel updates without polling — the parallel of
what the board's Events tab does off the hub's stream, above. The panel's own dashboard/leases reads keep a one-minute
poll as a backstop against a dropped frame rather than as the primary signal, and the session read carries no poll of
its own: a stream `401` routes into the same recovery seam a `401` from any other read does.

The runner's own web panel (issue #133) carries the same local brake as a second local door: a Pause/Resume control in
its top bar issues the identical `PATCH /api/runner` the CLI's local verbs use, so a click there is
`runner pause`/`runner start` by another name, not a second write path. Because the toggle can only ever move the local
brake, the panel also renders an explicit **Paused by hub** badge whenever the hub's own brake is set — an operator
whose local toggle reads "off" then still sees why the runner is not filling, rather than the toggle looking broken.
Clearing the hub's own brake stays hub-only, though — `blizzard hub runner resume`, never something reachable from this
panel.

The local brake has one **non-operator** trigger too: a configured runner spend ceiling engages this same brake
automatically when the fleet's rolling-window spend crosses it (see "Bounding fleet spend" below). It is the identical
brake — same "start no processes on this machine" semantics, live workers left to finish — so a runner can come back
`[paused: local]` with no `runner pause` ever issued. Clearing it is always an explicit operator action, never automatic
— `runner start` at the CLI, or the runner panel's Resume control, the same two doors that clear a hand-issued pause.
`blizzard hub status` names the reason on a ceiling-engaged runner so you can tell it apart from a hand-issued pause.

With no daemon running, the verbs report that rather than reading the store behind its back — a **client** verb reaches
the store only through the daemon that owns it. Two kinds of verb open the store directly instead. The **writing**
offline maintenance verbs are therefore run with the daemon *stopped*: `migrate`, `tick`, and the two transcript verbs
[`transcript backfill` and `transcript reship`](#shipping-transcript-content-to-the-hub--the-outbound-lane-off-by-default),
whose own refusal enforces it. The **read-only** `prompt status`, `prompt diff`, and `prompt install` open it for one
query — whether a workspace-prompt override stands — and need no such refusal, because the single-writer constraint the
refusal protects binds writers only. What you see from a client verb depends on how the daemon left:

| How it stopped             | On disk                               | What a local verb reports                                                 |
| -------------------------- | ------------------------------------- | ------------------------------------------------------------------------- |
| `systemctl stop` / SIGTERM | the socket is unlinked on the way out | `no runner daemon is serving at …` — start one                            |
| `kill -9`, OOM, reboot     | the socket file is left behind        | a connection error against that path — nothing is listening on the corpse |

Either way the next `host` start is clean: it clears a socket nothing is serving, and refuses to start beside one that
is still live (the store is single-writer).

## Chunk and runner control verbs, two axes — pause, restart, stop, complete, or detach a chunk; pause a runner (hub or local)

Seven verbs stop, re-aim, or settle work, and two of them share the word "pause," which is exactly where operators mix
them up. The five chunk-level verbs split along what they do to the claim: keep it (`chunk pause`, `chunk restart`),
give it away (`detach`), or end it for good, as either an abandonment (`stop`) or a hand-completion (`chunk done`, issue
#294).

"Restart" is the section's other overloaded word, and the two senses are unrelated. `chunk restart` below is this
operator verb, aimed at one chunk. **Daemon restart** — stopping and starting the runner process itself, and the
*restart-resume* that re-attaches its in-flight sessions afterward (issues #12, #13) — moves no chunk anywhere and is
never issued at a chunk; it is the ordinary `systemctl restart` of a service, covered under "The runner's two doors"
above.

- **`blizzard hub chunk pause <chunk_id>` / `chunk resume <chunk_id>`** (issue #46), or the board's **Pause**/**Resume**
  control in the chunk detail dock beside Detach — targets **one chunk**. On a chunk with a live claim, the runner kills
  that chunk's live worker but **keeps the claim**: the lease, route, epoch, held environments, and retry budget all
  survive untouched — only the process dies. Pause is also allowed on a chunk that hasn't been claimed yet (`ready`):
  there it holds the chunk out of the queue instead — it derives `paused`, not `ready`, so FILL skips it until it's
  resumed. `chunk resume` respawns a parked session **in place**, under the unchanged lease/epoch/session id, consuming
  no retry (a still-unclaimed chunk just re-derives `ready` and rejoins the queue). Refused (`409`) on a chunk that is
  `done`/`stopped`/`delivering`; deliberately **allowed** on `waiting_on_human`/`needs_human` — pause is a broad lever.
  (The `stopped` case in that refusal list — see below — was inert until `stop` existed to reach it.) The pause *fact*
  survives the answer to that question untouched (answering never un-pauses a chunk), but the *derived status* doesn't
  show `paused` while the question is open — a chunk both paused and parked on a question derives `waiting_on_human`
  first, so the board shows a `waiting_on_human` chip, not `paused`, until the question is answered. The dock still says
  so plainly and still offers **Resume** there — it reads the pause fact (`ChunkDetail.pause`), not the chip. Once
  answered, the pause fact is still there, so the chunk then derives `paused` (and stays parked) rather than resuming —
  `chunk resume` is what actually lets it go. `chunk resume` is idempotent — resuming an already-running chunk is a
  harmless no-op.
- **`blizzard hub chunk detach <chunk_id>`**, or the board's **Detach** control in the chunk detail dock (issue #42) —
  also targets **one chunk**, but the opposite direction: it **gives the claim away**. Both doors reach the same
  `POST /api/chunks/{id}/detach`, so either does exactly the same thing. The route is released, every held environment
  is freed, the lease closes, and the chunk re-derives `ready` so any runner — including a different one — can claim it
  next. Any live worker is abandoned along with everything else, not merely killed-and-kept. It is **not** requeue: no
  supersession fact is recorded and no epoch bumps, so a `needs_human` chunk detached this way is still `needs_human`
  afterward — only the route is gone. Refused (`409`) when the chunk has no live route left to release. See
  `blizzard hub chunk detach --help` for the CLI's full contract.
- **`blizzard hub chunk stop <chunk_id>`** (issue #118) — CLI/API only, with no board control today; there is no Stop
  button in the chunk detail dock the way Pause, Detach, and now Complete each have one, only
  `POST /api/chunks/{id}/stop`. The `chunk.stopped` fact is **irreversible** — there is no `un-stop` — but it is no
  longer guaranteed the last word on the chunk: an operator can still complete it afterward (see `chunk done` below),
  and the derived status then reads `done`, not `stopped`. It does **both** of what `chunk pause` and `detach` each do
  half of: it writes the `chunk.stopped` fact *and* releases any live route, so the holding runner frees the
  environments on its own next tick — no separate `detach` call needed. Unlike `detach`, a live route is not required:
  stop is allowed on `not_ready`, `ready`, and an already-detached chunk alike — the route release is conditional, not
  required. Stopping an escalated chunk also **closes its escalation** (issue #292; reaching `done` does the same): the
  chunk leaves the critical `needs-human` feed below and the holding runner drops it from `blizzard runner status` and
  its panel on the next PULL — so the composed resume command for the parked session goes with it, which is worth
  knowing before you reach for it, since there is no un-stop. Refused (`409`) only when the chunk is already `done` or
  `stopped` — not retroactive un-delivery, and not a lever for clearing a `delivering`/`waiting_on_human`/`needs_human`
  chunk back to a fresh state, only for ending it. See `blizzard hub chunk stop --help` for the CLI's full contract.
- **`blizzard hub chunk done <chunk_id>`**, or the board's **Complete** control in the chunk detail dock (issue #294) —
  a pure client of `POST /api/chunks/{id}/complete`, gated by `CHUNK_CONTROL` like every other control verb here. It
  writes its own `chunk.completed` fact — a hand-completion, not a synthetic reading of some other fact — reachable from
  **any** non-`done` status, including `stopped`: unlike `stop`, `chunk done` has no un-verb of its own either, but it
  is not foreclosed by having been stopped first. Between a `chunk.stopped` and a `chunk.completed` fact on the same
  chunk, the derived status favors whichever was recorded **later** (a tie going to the completion), so a chunk stopped
  and then hand-completed reads `done` afterward, not `stopped`. Releases any live route and held hub-exec slot in the
  same store transaction as the fact write, exactly as `stop` does, and its work-item refs become eligible for closure
  alongside a landed repo (`closable_work_refs`) — completing a chunk by hand closes out its issue the same way landing
  its repos would. **Idempotent, not refused**: completing an already-`done` chunk writes no second fact and is not a
  409 — deliberately asymmetric with `stop`, which stays refused on a `done` or `stopped` chunk. See
  `blizzard hub chunk done --help` for the CLI's full contract.

  **`stop` is not how a chunk reaches `done` — `chunk done` and the graph both are, and now either can follow a stop.**
  `stopped` records that an operator ended the chunk without it having delivered; `done` records that the chunk
  finished, either because the graph reached its reserved terminal (`to: done`, in the shipped graphs the
  `retrospective` node's `recorded` choice at the end of `deliver` → `retrospective`) or because an operator
  hand-completed it with `chunk done`. Unlike the graph path, `chunk done` needs no graph cooperation: it is a pure
  operator write, exactly like `stop`, and it is available *after* a stop as well as before one. So a chunk whose work
  you landed by hand, outside the fleet, no longer has to end at `stopped` as its truthful final record — stopping it
  and then running `chunk done` (or the board's Complete) marks it `done` instead, once you have confirmed the work
  actually landed. What remains foreclosed is going the other way: there is no `un-stop` and no `un-complete`, so once a
  chunk reads `done` — by either path — it stays there.
- **`blizzard hub chunk restart <chunk_id> [--node <name>]`** (issue #370) — CLI/API only, a pure client of
  `POST /api/chunks/{id}/restart`. It forces the chunk onto a node **now**, on a freshly minted session: the hub records
  the move at a bumped epoch, so the holding runner tears the running attempt down on its next tick and re-enters the
  named node with clean context. `--node` defaults to the chunk's current node, which is the common case — restart this
  step, the worker is thrashing; on a chunk that has never moved, that default is its graph's entry node. The claim is
  **kept**: route, tenure and held environments all survive, so the re-entry lands in the same worktree with the work
  already on disk, the artifacts the superseded step produced stay readable, and — like a pause, and unlike a failure —
  **no retry is consumed**, so restarting a thrashing step over and over never escalates the chunk you are rescuing. The
  bumped epoch is the guarantee, not the kill: a completion the displaced worker submits afterward is rejected as stale
  rather than advancing the chunk. Whatever parked the chunk goes with the move — an open ask is answered with a fixed
  system answer, an open gate decision is closed, an open escalation superseded — so nothing is left to re-park it at a
  node it is no longer on. Like `stop` and unlike `detach`, a live route is not required: an unclaimed `ready` or
  `not_ready` chunk moves too, re-entering the queue at the target node, and the next claim's envelope is what carries
  the move to whoever picks it up. Refused (`409`) when the chunk is `done`/`stopped`, when `--node` names no node on
  the chunk's own graph, or when the chunk stands on a node its own graph no longer carries — the position is refused,
  never silently rewound to the entry node. See `blizzard hub chunk restart --help` for the CLI's full contract.

  **Restarting an escalated chunk clears the hub-side row, and the runner's own list lags.** The move supersedes the
  escalation, so the chunk leaves the critical `needs-human` feed immediately — but the runner that raised it keeps
  listing it in `blizzard runner status` and its panel until something terminal happens to the chunk, exactly the lag
  the `stop` bullet above describes for itself.

  **Which brake outranks it.** The **per-chunk** `chunk pause` above does: a paused chunk parks as usual and honors the
  move on the tick after the pause lifts. Of the two **runner-level** brakes, the hub's does nothing to a restart at all
  — it gates new claims only, so the move lands and the holding runner still tears down and re-enters. The runner's own
  local brake defers the whole teardown: no worker is killed and nothing re-enters while it is on, because the re-entry
  is a spawn and the local brake starts no processes. The move stays recorded and the first tick past the brake honors
  it.

  **`restart` is not `migrate`.** `chunk migrate` records a **standing intent** — inspectable in `chunk show`,
  overwritable, `--cancel`able, and consulted only at the chunk's next transition; it is also the only verb that crosses
  graphs. `chunk restart` performs an **event** that has already happened when the call returns, within the chunk's own
  graph. Crossing graphs *and* starting clean is `migrate` then `restart`.
- **`blizzard hub runner pause <runner_id>` / `runner resume <runner_id>`** (the hub brake) and **`runner pause` /
  `runner start`**, or the runner panel's own Pause/Resume control (the runner's own local brake, issue #45 and issue

  #133 — see "The runner's two doors" above), are **per-runner**, not per-chunk. Neither kills any particular chunk's
  worker: the hub brake only stops that runner from claiming *new* work (every in-flight chunk, live worker included,
  runs on); the local brake additionally blocks every other spawn site (restart-resume, an answer-resume, a requeue
  respawn, …) but still never kills a worker that is already running — pausing locally is not a drain.

The distinction worth holding onto: `chunk pause` and `chunk restart` are the two chunk-level verbs that kill a live
worker while **keeping** the claim, and they differ in what they keep of the attempt — pause keeps the lease, epoch and
session so the resume lands in place, restart discards all three so the re-entry starts clean. `detach`, `stop`, and
`chunk done` all give the claim away (or end it), differing in whether the chunk can be reclaimed afterward and whether
it ends as `stopped` or `done`. The two runner-level brakes sit apart from all five: they never touch a live worker, and
they have no notion of "this one chunk" at all.

**A pause-parked chunk still occupies an agent slot.** FILL only ever claims new work into a runner's *open* slots, and
a chunk pause deliberately leaves the lease active and its environments held warm for the resume — that is what makes
the resume land in place instead of re-provisioning. So a paused lease counts against `max_agents` exactly like a
running one, with no worker consuming it. Pause enough chunks on one runner and it silently stops claiming new work — no
error, nothing beyond the pause's own log line — because every slot is spoken for by parked claims. Detach, stop, and
`chunk done`, by contrast, each free the slot immediately (the claim is given away, or ended, not held).

A restart into a **standing** chunk pause does not resume it — the runner checks the pause fact first, ahead of the
normal restart-resume path described below (see "The recovery contract"), so a chunk still marked paused when the runner
comes back is (re-)parked, not respawned. The claim is kept exactly as it would be if the pause had landed on a live
tick; only a chunk that was *not* paused resumes in place on restart.

### Taking over a parked session — `blizzard runner takeover`

`blizzard runner takeover <chunk_id>` continues a parked chunk's worker session interactively, in your own terminal. It
records a takeover fact with the daemon first — so no loop step can respawn or judge the session while you hold it —
then execs the harness's resume command as your terminal's child, and marks the takeover ended when you exit (even on
Ctrl-C). Run it as the service account, like every socket verb — see "The runner's two doors" above for what that means
and the `--dir`/`--runner-url` transport it addresses the daemon over.

Two things ride that exec which a plain copy-paste of a resume command does not get:

- **The configured permission mode.** The exec'd command reasserts `harness_permission_mode` from `blizzard-runner.toml`
  — whose scaffold default is `bypassPermissions`, meaning the session runs with **per-tool approval prompts disabled**,
  exactly as the daemon-spawned worker did. Set the knob to another mode (or empty, to omit the flag) if your deployment
  wants attended sessions prompted.
- **The lease's identity env.** The daemon returns a bounded set — the `BLIZZARD_*` identity vars plus its own `PATH`
  and `HOME` — which the verb layers over your terminal's environment, so the session's `blizzard runner` verbs
  (`attach`, `ask`, `artifact …`) reach the runner and the bare `blizzard` binary resolves to the deployment's venv.
  Opening a takeover **mints a fresh lease capability token** (invalidating the previous one); everything else about
  your shell — `TERM`, locale, your own variables — stays untouched, and nothing beyond that bounded set leaves the
  daemon. What actually authorizes those verbs is the **open takeover fact itself** (issue #291), not a fresh lease: the
  reference lease it names is very often already closed — the ordinary shape for a parked or escalated chunk — and the
  daemon resolves a worker verb's lease as that lease's own activeness *or* an open takeover naming it, so the session's
  verbs reach the runner against the same closed lease record the parked attempt held, unchanged in id, node and epoch.

For a **runner-composed** escalation, this makes the takeover verb, not the escalation record's raw string, the
supported way in. `blizzard runner status` still prints that raw string (`cd … && claude --resume …`) — that surface is
deliberately unchanged — and the board (issue #251) now renders the wrapped verb as the primary, copyable command, with
the raw string demoted to a collapsed "Unwrapped fallback" disclosure below it, present only when the escalation carries
one. Either way, the raw string resumes the transcript but deliberately carries **neither** of the above: pasted into a
bare terminal it runs at the harness's interactive permission default, with no identity env — that session can read and
edit, but its `blizzard runner` verbs cannot reach the runner.

Which command(s) a given escalation carries, and whether the underlying session is still reachable through the takeover
verb at all, is a domain fact governed by
[`blizzard-context`'s `domain/humans.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/humans.md)
§Escalation, not a deployment one — read there for which case produces which shape. Operationally, the one thing worth
knowing here: `blizzard runner takeover` checks the runner's actual held session state, never the escalation's own
composed commands, so it can succeed even against an escalation carrying neither. It refuses with `ChunkNotTakeable`
when that check fails — this runner does not hold the chunk, no resumable session sits behind its most recent lease, or
a takeover is already open — so on a split deployment run the verb on the runner's own host first: the wrong host
refuses with the not-held message even while the session is alive elsewhere. Only when no runner can enter the session
does resolving the escalation mean acting on the chunk directly (reading its bounce history or migration guidance) and
requeuing, not taking anything over — or, when the work has been finished outside the fleet entirely, stopping the
chunk, which closes the escalation with it (see the stop verb above).

A taken-over session also installs **no** heartbeat or session-end hooks: quitting it must not record a done-signal
against the lease, so liveness reporting stays a daemon-spawned-worker concern.

Ending the takeover ordinarily happens the same way it opened — a person exits the session and the CLI's own `finally`
PATCHes it closed — but the hub itself can end it too (issue #291): if the chunk transitions to a terminal status while
a takeover is still open, `PULL` closes the takeover fact on its own next tick, the same way it already mirrors an
escalation's own hub-side close. The end-PATCH is idempotent, so a session stopped from the board mid-takeover still
exits cleanly when its own `finally` reaches an already-closed takeover — it does not surface as a "could not reach the
runner" error.

### Editing an unclaimed chunk's build config

While a chunk sits **unclaimed** — resting `not_ready` (minted but not yet promoted) or promoted to `ready` with no
runner holding it yet — its pinned **graph** and its **default model/effort** are editable via `PATCH /api/chunks/{id}`
(below). Issue #120 widened this past its original `not_ready`-only window (issue #27): the wrong graph is often noticed
only after promote, with no runner anywhere near the chunk yet. Once the chunk is **claimed or later** — `running`,
`delivering`, `waiting_on_human`, `needs_human`, `paused` (post-claim), `done`, or `stopped` — these edits are refused
with `409`.

The **graph** carries one further condition the defaults do not (issue #271): the chunk must also **never have moved**.
Unclaimed and never-moved are not the same test. A chunk that was claimed, ran a node, and was then detached (see
"Detaching a chunk" above) derives `ready` again while still standing on a node of the graph it is pinned to, and
re-pinning it in place would leave its current node absent from the new graph — so that edit is refused `409` even
though the chunk is unclaimed. Moving a chunk that has run is not an edit's job, and which verb takes it depends on what
is actually changing: **which graph** the chunk is on is `chunk migrate` below (`--to-graph` is required, and a target
equal to the chunk's own current pin is refused `409`); **where on that graph** it stands is `chunk restart` above,
which crosses no graph and needs no re-pin. The defaults name no node to be stranded on, so they stay editable for as
long as the chunk is unclaimed.

`PATCH /api/chunks/{id}` (issue #124) applies any of `graph_id`, `default_model`, `default_effort`, and
`intended_migration` in one request, all-or-nothing: if any supplied field is outside *its own* editable window, the
whole request is refused (`409` — naming the field, except the already-moved refusal, which names the chunk and points
at migration) and nothing in the body is applied. The two defaults take the unclaimed window above and `graph_id` that
window plus never-moved; `intended_migration` — see "Migrating a claimed chunk to another graph" below — is different:
it is editable at **any non-terminal status**, claimed or not, so a `PATCH` naming it alongside a claimed chunk's
now-sealed `graph_id` still refuses the whole request on `graph_id`.

**The two defaults** (issue #144) are what a surface declaring neither inherits: effective precedence is a graph
`sessions:` declaration > the chunk default > the runner's own default. `default_model` is a **prioritized preference
list** in the same vocabulary a session declaration uses — a `blizzard:` tier alias or a harness-native model name,
resolved left-to-right at session mint; `default_effort` is a single value. Neither vocabulary is validated hub-side:
the alias tables live in each runner's own config, so both are opaque preference strings here. A blank entry is `422`;
an empty list and an explicit `null` effort are real values — *express no preference*, the state ingest mints — not
"leave unchanged".

From the CLI, `--default-model` is repeatable and **ordered**:

```text
blizzard hub chunk set ch_… --default-model blizzard:advanced --default-model blizzard:basic \
  --default-effort high
blizzard hub chunk show ch_…     # reads both back
```

There is deliberately **no web editing surface** for either — the chunk detail dock's model editor was removed with
`Chunk.model`, and is not replaced. `chunk show` (or the detail payload's `default_model`/`default_effort` fields) is
the read-back.

A graph edit has two further distinct `409`s beyond the status window. Targeting a graph that has been **retired** (see
"Graph lifecycle — retire and re-enable" below) is refused even on an otherwise-editable chunk, naming the retired graph
id rather than the chunk's status. Editing a chunk that has **already moved** is refused as above. The already-moved
check runs **first**, so a moved-but-unclaimed chunk aimed at a retired graph reports the move, not the retirement.

### Migrating a claimed chunk to another graph

`blizzard hub chunk migrate <chunk-id> --to-graph <graph> [--node <name>] [--cancel]`, or `PATCH /api/chunks/{id}`
`intended_migration` (issue #124) — sets a **standing intent** to move a chunk onto another graph, consulted (never
applied eagerly) at the chunk's *next* transition. Unlike the stop-work verbs above, it does not stop or interrupt any
in-flight work: the current attempt runs to its normal verdict, and only that transition either fires the intent or, for
`auto` mode with no name match, leaves it set for the transition after. `--to-graph` names a graph id or a graph name
resolved to the newest enabled graph of that name; a blank name, a retired target, or a target equal to the chunk's own
current pin is refused (`409`). With no `--node`, the intent is `auto`: it fires only when the transition's own
destination node name also exists on the target graph, landing there; with no name match the transition applies
unchanged and the intent stays set for next time. `--node <name>` makes it `forced`: it fires unconditionally at the
next transition, landing on the named node regardless of the transition's own destination — refused (`409`) up front if
that node does not exist on the target graph. `--cancel` (or `PATCH` with `intended_migration:
null`) clears a standing
intent without firing it.

Editable at **any non-terminal status** — `not_ready` and `ready` too, not just once claimed — since the intent is a
plain mutable chunk property, not a transition itself; it is only ever *consulted* at a transition, which is why in
practice it matters once a chunk is claimed and progressing, and why it complements rather than replaces the never-moved
graph repin above — it is the only way to move a chunk that has run. When the intent fires, the chunk's movement is
recorded as a migration exactly like an authored cross-graph judgement choice (see "Graph lifecycle" below): it re-pins
the chunk's graph, lands it on the resolved node, and clears the intent in the same write. Landing governs by the landed
node's own executor — a migration landing on a hub-executed node derives `delivering`, exactly as a transition into one
does. See `blizzard hub chunk migrate --help` for the CLI's full contract.

### Following the latest mint automatically

The intent above is per chunk and aims at one resolved mint **id** — deliberately, so a later mint under the same name
never silently redirects a chunk an operator aimed by hand. The cost is that every workflow edit strands the fleet on
old mints until each in-flight chunk is migrated individually. `follow_latest` (issue #164) is the standing policy that
removes that chore: a chunk pinned to a follow-latest graph re-pins to the newest *enabled* mint of its own graph's
**name** at its next transition.

Two levels, and the graph wins where it speaks:

| Where                                                              | Value                              | Meaning                                                                            |
| ------------------------------------------------------------------ | ---------------------------------- | ---------------------------------------------------------------------------------- |
| `follow_latest` in `blizzard-hub.toml`                             | `true` / `false` (default `false`) | the fleet-wide default for every graph that says nothing                           |
| `blizzard hub graph follow-latest <graph-id> true\|false\|inherit` | `true` / `false` / `null`          | this mint's own override; `inherit` (the default for every mint) defers to the hub |

The shipped default is `false`, so landing this changed nothing until someone opts in. The policy is set **per mint**,
not per name: a chunk consults the policy of the graph it is pinned to, so arming a lineage means arming the mint its
chunks sit on — or setting the hub default, which covers every name at once. `GET /api/graphs/{id}` serves the stored
tri-state as-is, so a reader can tell "this graph says nothing" from "this graph says false".

It rides the same deferred path as an explicit intent, with the same guarantees: nothing in-flight is interrupted, the
move is recorded as a **migration** fact rather than disguised as a transition, and it fires only at a transition the
chunk was making anyway. Landing is name-match-else-entry on that transition's own destination — the chunk goes where it
was already going, just on the newer mint, falling back to the target's entry node when the newer definition no longer
has that node at all.

An explicit `intended_migration` **outranks** the policy: if a chunk carries one, the policy is not consulted at all,
including on a transition where an `auto` intent falls through for want of a name match. The policy is otherwise a plain
no-op — no error, no fact — when the chunk is already on the newest mint, when every newer mint is retired, or when the
effective policy resolves `false`. It will never move a chunk *backwards*: if a chunk's own mint has been retired so
name resolution answers with an older one, the chunk stays where it is.

## Graph lifecycle — retire and re-enable

`blizzard hub graph list` / `graph retire <graph_id>` / `graph enable <graph_id>` (issue #101), or the graph explorer's
own **Retire** / **Re-enable** buttons and lifecycle badge in the web board — an operator's brake over which graph a
**name** resolves to. Not a work-stopping lever like the four verbs above: a graph carries no chunk, no claim, no live
worker to interrupt. Retiring never touches the graph's own immutable row — it appends a `graph.retired` fact, reversed
by `graph enable`'s `graph.enabled` fact — so the brake is **reversible**, and every toggle is itself an append-only
audit trail rather than a destructive edit.

**What retiring changes, and what it deliberately leaves alone.** A chunk that already pins a retired graph keeps
running it to completion — existing pins are left to run out; issue #101 is scoped to blocking only *new* resolution by
name, never touching a chunk mid-workflow. What a retire blocks is every name lookup: the default-graph pin at ingest
and a cross-graph migration's `graph:<name>` judgement target both resolve through the newest **non-retired** graph of
that name, skipping every retired `graph_id` entirely.

**Retiring every version of a name is a real trap, not a hypothetical.** If every graph ever minted under one name —
including the packaged `default-delivery` the hub ingests against out of the box — is retired, name resolution has
nothing left to hand back. The next ingest that would otherwise lazily mint a fresh copy of the packaged default
**refuses with `503`** instead: minting a fresh copy there would be immediately effective and would silently undo the
retire the moment it landed, including across a hub restart. Re-enable one of the retired versions, or mint a new graph
under that name, to clear it. A cross-graph migration choice naming an all-retired target has the same "nothing left to
resolve" shape at the moment a chunk takes it.

## Bounding fleet spend — cost caps and the spend kill-switch

An unattended overnight fleet spends against the operator's harness billing with no ceiling by default. Two optional
caps bound it, both configured in a `[cost]` table in `blizzard-runner.toml` and both **absent by default — no `[cost]`
table means no cap and no ceiling, exactly the prior behavior**. Cost figures are the harness's own `total_cost_usd`;
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

- **Per-chunk cap (`chunk_cap_usd`).** Checked **between attempts**, never by killing a live worker: when a chunk's
  derived total cost reaches the cap, the runner parks it `needs_human` at the next step boundary with an escalation
  naming the cap and the spend, and the usual takeover command to resume. A capped chunk is not a failed one — no retry
  is consumed. Resuming is a human decision: raise or clear the cap, then requeue the chunk and it proceeds.
- **Runner ceiling (`runner_ceiling_usd`, `window_hours`).** Checked at each tick: when this runner's spend over the
  trailing `window_hours` crosses the ceiling, the runner's **local pause brake** engages (the same brake `runner pause`
  sets — every spawn site suppressed, no retries consumed, live workers left to finish), carrying the ceiling and the
  spend as the pause's own recorded reason. Unlike the per-chunk cap, it raises no escalation: the ceiling is
  runner-scoped, so there is no one chunk to park. The window is a rolling last-N-hours sum; **it does not
  auto-unpause** when the window later rolls the spend back under the ceiling. Clearing the brake is always an explicit
  operator decision, never automatic — `blizzard runner start` at the CLI, or the runner panel's Resume control, exactly
  as for a hand-issued pause. `GET /api/runners` and `blizzard hub status` surface the ceiling reason on the paused
  runner, so it reads differently from a manual pause.
- **Cost-absent rows are a conservative lower bound.** When a worker crashes or is `kill -9`ed before the harness emits
  its final usage envelope, blizzard records the attempt's tokens from the session transcript but its **cost is
  genuinely unknown** — so an absent-cost row contributes its tokens but **$0** to the cost sum, making the total a
  lower bound, flagged **PARTIAL** wherever it is shown (a `~` marker on the board and in `hub status`). Both caps trip
  on this lower bound and surface PARTIAL, each on its own carrier — the per-chunk cap in the escalation it raises, the
  runner ceiling in the reason recorded on the pause — so an operator knows the true spend may be higher, and a cap
  never silently under-counts a crash-heavy chunk into looking cheap.

See `blizzard hub status` for the per-chunk cost column, the fleet total, and a paused runner's ceiling reason; the
board's chunk cards and detail dock show the same figures live.

## Surfacing subscription rate-limit windows — an advisory-only board read

A harness that runs under a metered subscription plan — Claude Code's OAuth-backed plan is the first — tracks its own
account's rate-limit window utilization independently of anything blizzard spends or caps. The runner can sample that
figure on a configurable cadence, controlled by an `[external_subscription_usage]` table in `blizzard-runner.toml`,
**absent by default — no table means the default cadence below, exactly the prior behavior**.

```toml
[external_subscription_usage]
# How often the runner samples Claude Code's own OAuth usage endpoint, in seconds.
# Defaults to 300 (5 minutes) when the table (or just this key) is absent.
sample_interval_seconds = 300
```

- **Claude-Code-only, today.** Claude Code is the only harness adapter that ships, and the only one with a subscription
  concept to sample. The seam's contract already covers a harness that has none — it reports no sample rather than a
  figure — and a runner with no sample renders as the usage block simply being absent on the board, never a fabricated
  zero or empty reading.
- **Advisory only.** The sampled utilization never throttles or backpressures claiming, scheduling, or spawning in any
  way — it is a read for a human, not an input to the runner's own decisions. Nothing about cost caps, the spend
  kill-switch, or work claiming changes based on it.
- **Credentials never leave the runner machine.** The sample step reads the runner's own local OAuth credential file to
  authenticate the usage request; only the derived utilization percentages, window labels, and reset times cross the
  wire to the hub — the bearer token itself is never reported, stored, or forwarded.
- **`blizzard runner external-usage probe`** is a read-only diagnostic that samples once and prints the parsed snapshot
  without writing to the store, ticking, or reporting anything to the hub — useful for confirming the runner's
  credentials and cadence are working before waiting on the next scheduled sample.

See the runner panel on the board for a paced-window bar per sampled window (`5h`/`7d` for Claude Code), rendered only
when a runner has a non-stale sample to show.

## Warning on a worker's session context — the warn lane (off by default)

A graph's `rotate` bounds are evaluated when a node-step is about to *start*, against the session it would resume. That
leaves the inside of a long-running invocation unobserved, which is where a session's context actually grows. The warn
lane samples a **running** lease's session context on a cadence and reports the first time it crosses a line you set:

```toml
[context]
warn_tokens = 300000
sample_interval_seconds = 60
```

- **Absent `warn_tokens`, the lane is off entirely** — no transcript is read at all, so a runner that never opts in pays
  nothing for it. `sample_interval_seconds` alone does not turn it on; it is inert until `warn_tokens` is set.
- It **observes, and gates nothing.** No worker is stopped, no chunk is parked, and no session is rotated by anything
  here — that decision belongs to a graph's `rotate` block. The spend controls that *do* intervene are `[cost]` above.
- The warning fires **once per lease**, on the first crossing, not once per sample. A session past the line goes on
  being sampled without re-reporting.
- The measurement is the conversation's size — the prompt size of the session's newest turn, what a resume of it would
  pay for again — not tokens accumulated across the run.

A crossing surfaces as a `worker-context-warned` entry in the operational event feed — the board's event log, and
`GET /api/events` below — carrying the lease, node, measured context, and the configured line. The runner also logs it
locally at WARNING. Unlike `[external_subscription_usage]`, this lane ships no read-only probe verb: to confirm it is
sampling before waiting on a crossing, read the runner's journal, or the `context_samples` table in `runner.db`
directly.

## Shipping transcript content to the hub — the outbound lane (off by default)

Alongside the fact lane (`lease.minted`, completions, and the rest), the runner carries a second, structurally
independent outbound lane for transcript content: normalized turns read from the harness's own session transcript,
sliced into turn-range records and pushed to the hub over their own route, buffering through a hub outage exactly like
the fact lane's own store-and-forward does. A wedged or slow transcript FLUSH never delays a completion or a gate
decision — the two lanes share nothing but the runner process. The hub stores what it accepts, compressed at rest,
behind an operator-only read API (blizzard#247) — this is a durable transcript, not a discard sink.

The one exception is the READ half, not the flush: a closing lease's own still-open segment is pumped one last time
before the closure it gates is recorded, bounded by a `PUMP_LEASE_MAX_SECONDS` (5s) budget checked only between reads —
so one slow in-flight read can still push closure past that bound, and a raised exception is isolated (never fails the
closure) but not free of delay either. This is deliberate: draining what a closing segment can before it stops being
pumpable is worth a bounded wait, not zero delay.

```toml
[transcripts]
# Ship transcript records to the hub. Off by default: with no [transcripts] table (or
# `ship` omitted), the runner reads no session content and enqueues no record. The switch
# gates non-final records alone — every open segment still ships its own final record at
# lease closure regardless, the same unconditional close-out the fact lane's own facts
# get. A segment that never had a single successful read is no exception: a normalizer
# version is a static per-harness constant, so its final record declares the source's own
# "never ran" sentinel rather than shipping nothing.
ship = false
```

- **`ship = false` does not mean the lane is silent.** Every closed lease still enqueues and attempts to flush its final
  marker regardless of `ship` (above). Against a hub that does not yet serve `/api/fleet/transcripts` — an older hub
  version, most commonly — that flush fails every tick: the marker buffers forever (never draining) and a transport
  error logs each attempt. This is the same store-and-forward behavior the fact lane already has against any unreachable
  route; it is not a data-loss risk, but "off by default" alone does not prepare an operator to expect it. And once that
  flush *does* succeed — a hub new enough to accept it — the final marker still lands in the hub's segment index even
  with `ship = false`: a content-free row (`turn_range_start=0, turn_range_end=-1,
  turns=[], truncated=false`) per
  chunk closure. Its presence alone does not mean real transcript content was captured; it means only that the lease
  closed.
- **Capped at both ends, independently.** The runner enforces its own 8 MB per-record cap and 64 MB per-chunk budget as
  the well-behaved case (D4): an oversized turn's own text, its tool call's output, its tool call's input (any nested
  sidechain turn's, too), are all shrunk in place rather than dropped, so the runner's read position still advances past
  it. A record still over cap once nothing is left to shrink — structural overhead alone — ships instead as an
  empty-turns slice over the same claimed range, never as an over-cap body. Past the 64 MB chunk budget, the runner
  stops shipping that chunk's content but still ships every open segment's final record. The hub enforces its own,
  independent caps as the rogue case — 10 MB/record, 64 MB/chunk, 2 GB/runner/day — rejecting an over-cap record but
  still acknowledging it, so a misbehaving runner wastes its own budget without wedging its lane. The hub's per-record
  cap sits deliberately *above* the runner's: over the runner's, content is shrunk and every turn survives; over the
  hub's, the whole record's turns are rejected and stored as `[]`. Every runner-side outcome above is recorded on the
  segment and surfaced as a `warning` operational event (see [the event log](#operational-visibility--the-event-log)
  below), the same way a captured command failure is.
- **Every ceiling above is a default, not a fixed limit.** Each is overridable under `[transcripts]` in the owning
  daemon's config — `record_max_bytes` and `chunk_max_bytes` on the runner, `record_max_bytes`, `chunk_budget_max_bytes`
  and `runner_daily_rate_max_bytes` on the hub. Widen them for a backfill window — `blizzard runner transcript reship`
  spends the per-chunk budget a second time over the same chunk — then restore them, keeping the runner's per-record cap
  at or below the hub's so the ordering above still holds. **A daemon resolves its ceilings once, at startup**: edit the
  file, then restart that daemon, or the old values stay in force with nothing in the output saying so. A bad value
  fails loud rather than falling back to the default — a non-integer or non-positive cap is a config error, and on the
  hub that fails the `migrate` step the unit runs before `host`, so the daemon does not come up. Two limits widening
  does *not* lift: a proxy in front of the hub rejects an oversized body before ingest ever adjudicates it (raise
  [`client_max_body_size`](#behind-a-tls-terminating-reverse-proxy) with the hub's record cap), and a chunk whose
  shipping already stopped stays stopped — the per-chunk budget latches on the segment, so widening frees the next
  segment, never the stopped one.
- **Late links, for content whose call shipped earlier.** The runner reads a session in windows, so a tool's result and
  a subagent's conversation routinely arrive in a later record than the call that produced them. Both ship carrying the
  `tool_use_id` of that call — a result as a `tool` turn flagged `output_patch`, a conversation as a top-level
  `sidechain` turn naming `parent_tool_use_id` — and the board folds each back onto its call when rendering. Reading the
  API directly, expect to see them unmerged; a late turn whose call is outside the segment you fetched stays standalone
  rather than being dropped. A conversation whose spawning call the runner never observed at all remains genuinely
  unlinkable: it is dropped, and says so as a `warning` event naming the segment.
- **Reads are operator-only.** A transcript holds everything a worker saw; reading one back
  (`GET /api/chunks/{id}/transcripts` and its per-segment content route) requires the `transcript:read` permission,
  `contributor` role and above — a runner's own fleet-plane token can push to the ingest route but can never read one
  back.
- **History predating the lane is imported by hand**, with `blizzard runner transcript
  backfill` (blizzard#250). It
  walks this runner's own lease records — never a sweep of the harness directory, which on a working machine is mostly
  the operator's own sessions — opens a segment per session whose file it can still read, and enqueues it onto the same
  outbound lane as an ordinary segment. Rerunnable by design: a session that already holds a segment is skipped, and a
  session this run could not finish stays open for the next run to resume rather than being closed out, so a rerun costs
  the hub a duplicate turn range at worst — its natural key discards that.

  **Run it as the runner's own user, with the runner's own environment, and with the daemon stopped.** All three are
  load-bearing under the unit this document prescribes (`User=blizzard`, `EnvironmentFile=`, `--dir`), and only the last
  is enforced:

  - **Daemon stopped** — the verb writes the store, which is single-writer. It refuses while anything holds the
    runtime's socket, and fails *closed*: a daemon that is wedged or answers unhealthily still counts as holding it.
  - **The runner's user** — with no `transcripts_root` set, transcripts are read from `$HOME/.claude/projects`. Run as
    anyone else and every session reads as *gone*.
  - **The runner's environment** — the hub token comes from the process environment. Without it every flush is refused,
    and the sessions stay buffered rather than reaching the hub.

  `--dry-run` classifies without opening, draining or shipping anything; its counts are what a real run would *attempt*,
  not a promise (a real run flushes between sessions, so backpressure moves the line). `--limit N` bounds one run —
  worth using on a long history, since the hub enforces a per-runner daily rate (2 GB by default, widenable via
  `runner_daily_rate_max_bytes`) and content past it is rejected rather than queued. The verb refuses outright while
  `ship = false`.

  The counts mean: *imported* — read and enqueued to the outbound lane, which is not the same as accepted by the hub;
  *capped* — the subset of those the hub refused or the runner stopped shipping, reported separately for exactly that
  reason; *gone* — no readable transcript found for that session by **this run** (usually a file the harness rotated
  away, but a wrong root or user reads identically, and nothing is written either way, so a rerun retries it);
  *deferred* — left for a rerun, whether by `--limit`, by outbound backpressure, or because the session could not be
  read to its end this time.

  One imperfection is accepted rather than worked around: a pre-lane session resumed in place recorded no resume
  offsets, so it imports as one merged segment attributed to the lease its session began on, rather than splitting at
  its resume seams.

- **A segment already shipped can be sent again**, with `blizzard runner transcript reship
  SEGMENT_ID`. It is for a
  segment the hub holds in a form this runner has since outgrown — most often one an older, smaller per-record cap
  shrank. It carries **the same three run conditions as `backfill` above** (runner's user, runner's environment, daemon
  stopped), enforced by the same refusal.

  **It supersedes rather than corrects, and the duplicate is the mechanism, not a bug.** The hub's ingest is idempotent
  on `(segment_id, turn_range_start)` and never overwrites a record it already accepted, so a short segment cannot be
  fixed in place — only followed by a second one carrying the same lease's content. Both then stand: the chunk board's
  Transcripts tab lists **two** segments for that lease, and the source is deliberately left exactly as it shipped so
  the record of what the hub was once told stays honest. Decide that is what you want before running it.

  A rerun resumes the same superseding segment rather than opening a third; the verb refuses a segment whose lease is
  still active, because a live lease's segment belongs to the running pump. Three warnings can follow the report line,
  and each means something different: *shipping is stopped for this chunk* — nothing was sent at all, and note that a
  re-ship spends the per-chunk budget (64 MB by default) a **second** time; *the source was not read to its end* — the
  new segment stays open, rerun to resume it; *the new segment is itself marked truncated* — check the reason in the
  event log, since only some reasons are about the cap.

## Deriving events from transcripts — the analytics lane (blizzard#254)

Alongside the raw transcript segments above, the hub maintains a derived, queryable **event stream**: one row per
interesting occurrence a segment's turns hold — today, a file read naming a concrete path, a skill invocation, or an
agent spawn — stamped with the node-step context the segment already carries (chunk, node, epoch, spawn generation),
tagged with sidechain nesting depth and the nearest-enclosing agent type, and stamped with the extractor version that
produced it.

**Automatic, no configuration.** A standing in-process sweep derives every final, not-superseded segment's events on its
own interval; there is nothing to enable and nothing in `[transcripts]` gates it — it runs whenever segments exist to
derive, independent of `ship`. A segment's events typically appear within one sweep interval of the segment landing,
with no operator action.

**It is a projection, not a second source of truth.** Every row is a pure, immutable computation over the segment's own
stored content — never itself authoritative, and never gating a claim, a spawn, or any admission decision. Dropping the
whole table costs only the time to re-derive it, which the sweep does on its own the next time it runs; nothing else
needs to change and no downtime is required. If a segment's stored content later changes underneath an earlier
derivation (a rejected record accepted, a late record landing), the sweep detects it and re-derives that segment on its
next pass.

**Forcing it sooner: `blizzard hub analytics re-derive`.** The standing sweep is usually enough, but an operator who
wants a specific segment's events immediately, or who bumped the extractor version and wants a chunk (or the whole
fleet) re-derived now rather than waiting for the sweep's own cadence, can force it:

```text
blizzard hub analytics re-derive --segment SEGMENT_ID
blizzard hub analytics re-derive --chunk CHUNK_ID [--limit N]
blizzard hub analytics re-derive [--limit N]
```

`--segment` forces that one segment regardless of whether it currently looks up to date. `--chunk` (or neither option,
for the whole fleet) derives up to `--limit` (default 50) of that scope's current candidates and reports how many remain
— a nonzero `remaining` means calling it again continues from where it left off, the same way the standing sweep itself
converges. **No downtime**: it runs the same in-process reconciler already live inside the hub, driven directly rather
than waiting for its next tick — never a `--dir` verb, and the hub is never stopped for it. It requires `admin`+
(`analytics:admin`), since it mutates, unlike the read-only `transcript:read` grant the segment routes above are gated
on.

### Reading events and counts — `blizzard hub analytics events`/`summary` (blizzard#255, #257)

Two operator verbs read the derived event stream above — `blizzard hub analytics
events` for the raw per-occurrence
rows, `blizzard hub analytics summary` for four canned occurrence counts — thin wrappers over
`GET /api/analytics/events` and `GET
/api/analytics/counts/*`, gated on the same `transcript:read` grant the segment
routes above use:

```text
blizzard hub analytics events [FILTERS] [--cursor CURSOR] [--limit N] [--ndjson]
blizzard hub analytics summary counts-files       [FILTERS]
blizzard hub analytics summary counts-skills      [FILTERS]
blizzard hub analytics summary counts-agent-types [FILTERS]
blizzard hub analytics summary counts-nodes       [FILTERS]
```

`FILTERS` is the shared vocabulary every event-projection route understands: `--graph`, `--source`, `--since`, `--until`
(the four fields every analytics dataset in this doc shares), `--extractor-version` (defaults to the version the
standing sweep currently derives; naming an older one reads that version's own rows rather than mixing them, since
mixing would double-count the same occurrence), and the four event-shape filters `--kind`, `--tool`, `--subject-prefix`,
`--node`. Each `summary` counts dataset only takes the subset its own route exposes — `counts-files` skips `--kind`
(fixed to `file_read`), `counts-skills` takes only `--extractor-version`/`--node` of the six (a skill name is already
flat, so a prefix or tool narrows nothing), and `counts-nodes` skips `--node` (naming one would select a single group
rather than narrow the count). A flag a chosen dataset does not expose is refused with a `does not apply to dataset`
error, never silently dropped.

`events` has three output modes: a human table by default; `--json`, which prints the raw single-page envelope including
its `next_cursor` — the same page `GET
/api/analytics/events` itself returns; and `--ndjson`, which streams every
matching event from `GET /api/analytics/events/ndjson` to stdout, one JSON object per line, unbounded and unpaged —
incompatible with `--json`, `--cursor`, and `--limit`, since the streaming route takes neither. `--since`/`--until` are
read in the operator's own local wall clock and converted to UTC before crossing the wire, not merely relabeled — a bare
`--since 10:00` means 10am the operator's own clock, whatever timezone the hub process itself runs in.

A bare 401 gets the `blizzard hub login` hint; a bare 403 (missing `transcript:read`, or a runner token presented on an
operator verb) surfaces the API's own `detail` message unchanged, rather than a generic refusal.

### The operational datasets — durations, spend, outcomes (blizzard#256)

Alongside the derived event stream, the same namespace exports the fleet's operational numbers as read-shaped datasets:
step durations, tokens and spend, and node failure/retry outcomes — the numbers already computable from board-serving
reads, reshaped for bulk export rather than a per-chunk detail view. No new facts are stored; every dataset is derived
at read time from primary sources — `transitions`, `lease_facts`, `usage_facts`, `chunk_bounces`, and `chunk_migrations`
— joined against `chunks`, `graphs`, `graph_nodes`, and `chunk_work_refs` for graph/source resolution.
`blizzard hub analytics summary` reads all six, one dataset per call:

```text
blizzard hub analytics summary durations-nodes  [FILTERS]
blizzard hub analytics summary durations-graphs [FILTERS]
blizzard hub analytics summary spend-nodes      [FILTERS]
blizzard hub analytics summary spend-graphs     [FILTERS]
blizzard hub analytics summary spend-chunks     [FILTERS] [--cursor CURSOR] [--limit N] [--ndjson]
blizzard hub analytics summary outcomes-nodes   [FILTERS]
```

Every dataset here takes only the shared `--graph`/`--source`/`--since`/`--until` filters the events/counts datasets
above also share — no `--kind`/`--tool`/ `--subject-prefix`/`--node`, since this family groups over
`transitions`/`usage_facts`/ etc., not the event projection — and the same `transcript:read` gate, strictly narrower
than the `fleet:view` gate the same spend numbers already sit behind on the board. What `--graph` narrows *by* differs
per dataset, though, so two datasets' numbers for one graph are not directly comparable for a chunk that has migrated:
durations and outcomes' judged half filter by the transition's own graph, spend filters by the chunk's *current* graph
pin, and outcomes' failure half uses the failed attempt's own derived graph — each dataset's own wire model states its
resolution; this is not one shared meaning. `spend-chunks` is the one dataset that pages or streams:
`--cursor`/`--limit` (default 200) page the human/`--json` output, and `--ndjson` streams the whole filtered set from
`GET /api/analytics/spend/chunks/ndjson` to stdout instead — the same `--json`/`--cursor`/`--limit` incompatibility
`events`' own `--ndjson` carries, since the streaming route takes neither either. Per-node and per-graph groupings
return one JSON envelope each, with no cursor of their own — `node_id`/`graph_id` are per-graph-*version* ids, so the
*ceiling* on either envelope's size grows every time a graph is minted, whatever the fleet has or hasn't run; the size a
caller actually receives does not, since a grouping only emits keys with matching activity — an unrun graph mint adds
zero rows to what ships. Unlike `events`' own NDJSON export, the chunk-spend one is not a point-in-time snapshot: each
page's sums are recomputed at page-fetch time, so a chunk emitted on an early page can be invalidated by a usage fact
recorded while a later page is still streaming. Field-by-field shapes are the committed `openapi/hub.openapi.json`'s own
record — the wire models that generate it are each dataset's one prose home, not restated here.

Seven callouts worth an operator's attention rather than a field's own doc comment:

- A duration is hub-observed wall-clock time, not runner-measured — see the `AnalyticsDurationView` wire model
  (`openapi/hub.openapi.json`) for the parked-gate vs. delayed-flush directions this doesn't restate here.
- Durations and the judged-choice distribution cover a step completed by an ordinary transition only — a hub-executed
  node's own exit transition has no measurable wall-clock interval of its own, so durations excludes it, and a step
  completed via a cross-graph migration (an authored edge, an intent, or follow-latest) is invisible to both, since
  neither reads `chunk_migrations`. A documented gap, not a silent one.
- Outcomes reports a judged-choice distribution and a retry-consuming attempt-failure count as two separate numbers,
  never one blended failure rate — a delivery kick-back counts as neither, including the kick-back's own routing
  transition, which shares the bounce's epoch and is excluded from the judged count for exactly that reason.
- Outcomes' two counts window on different instants (the judging transition's own vs. the failed attempt's lease mint),
  so a boundary case can count on one side only.
- None of the seven routes defaults or requires a `since`/`until` window — unlike `/api/spend` (which requires `since`)
  or `/api/activity` (which defaults to 24h) over overlapping fact tables. A deliberate, still-open deferral: an
  unfiltered call costs a full scan of whatever it reads, acceptable at today's real fleet volumes but not bounded by
  anything this namespace enforces.
- The chunk-spend NDJSON export (`spend/chunks/ndjson`) pages through `usage_facts` — the hub's highest-cardinality fact
  table, with no index on `chunk_id` — 500 rows at a time, re-scanning the full table on every page. Exporting the
  fleet's whole per-chunk spend costs `ceil(N/500)` such scans; a deliberate deferral alongside the unbounded window
  above, not yet paired with an additive index.
- Every analytics read shares the hub's default connection pool (5 + 10 overflow) with the fleet's own write path — no
  dedicated budget is carved out. A burst of concurrent unfiltered analytics calls can hold enough connections to make
  an unrelated write wait out the pool's checkout timeout; a deliberate, undeclared deferral, not a magnitude this
  namespace bounds today.

## Operational visibility — the event log

The failures that cost the most are the least visible: a worker that exits without recording a completion and leaves its
chunk sitting `running` behind a dead process, a spawn/push/declare command that failed on a missing environment var, a
stall past the liveness window. A chunk's *status* says it is stuck; it does not say *why*. The hub owns a durable,
append-only, typed and **severity-ranked** operational event log that does.

- **The runner reports failure events.** When a worker exits non-clean, when a captured spawn/push/environment-prep
  command fails, or when an attempt is reaped/abandoned/escalated, the runner emits an operational event on the same
  durable store-and-forward path completions ride. The hub folds each into the log. Severities are `info`, `warning`,
  and `critical` — a **closed** set, because the feed ranks by it and orders anything else below every other row.
  Runner-emitted examples: `info` (an attempt given up because the chunk moved on), `warning` (an attempt failed and
  will retry, or a command failed), `critical` (a worker lost to a human, retries exhausted).
- **The hub emits its own events too**, for the failures only it sees. Chief among them, `hub-node-unroutable-outcome`
  (`critical`): a hub command node produced an outcome its graph authors no edge for, so nothing routes and the chunk
  re-polls that same outcome **forever**. It is announced once per node visit, not once per poll. The remedy is not a
  retry — author the missing edge on the graph, then requeue. The work-item closure events (`work-item-closed`,
  `work-item-close-failed`) are the hub's too — see "Closing delivered work items" above.
- **`GET /api/events`** returns the log newest-and-most-severe first, filterable by `severity` / `runner_id` /
  `chunk_id` / `since`, with a bounded default page. Existing escalations appear in the *same* feed as a `needs-human`
  event kind — `needs_human` is one row in one surface, not a place to look separately. A row leaves the feed when its
  escalation is superseded, which any of four things does: a requeue, an operator `chunk restart`, the next attempt's
  lease, or the chunk ending — stopped or done.
- **The board's Events tab** renders the feed live: new events fan out over the existing SSE spine
  (`/api/events/stream`), so an open board updates without polling. Each row links to its chunk.
- **`GET /api/activity`** is a second, differently-shaped operator read: the board's Event log rail backfills from it on
  page load rather than starting empty and waiting for live traffic. It merges three already-durable sources —
  chunk-level status changes, the operational event log above, and runner pause/resume — newest-first, bounded by
  `since` (default 24h before now) and `limit` (default/max 200, `1..1000`), gated the same as `GET /api/events`.
  Ordering is pure recency, not severity-then-recency: this is a recent-activity feed, not the operational log's triage
  view. After the initial backfill the rail continues live over the same `/api/events/stream`, deduped against the
  backfilled rows by each frame's fact-identity `key` rather than by timestamp.

The event log makes failures **visible in-product**; it does not repair the underlying failure modes (a missing
spawn-env var, a `SessionEnd` hook that never fired) — those are fixed at their source. It is append-only with no
rotation policy beyond that.

## Kiosk demo mode — an unattended board on a wall screen

`?demo=true` on any board URL hands the board to an automatic tour, for a screen left running in a room rather than
watched by an operator. Nothing in the UI announces it and nothing links to it — the query string is the whole switch.

```text
https://hub.example.com/board?demo=true
```

One cycle, repeated forever: a chunk is picked at random off the live fleet, opened on the board, and its detail dock
scrolled slowly to the bottom; the tour then descends into that chunk's Artifacts tab and shows a random artifact at a
time, each scrolled top to bottom across its dwell; at the end of the cycle it swaps to another chunk. A chunk with no
artifacts ends its cycle early rather than holding an empty viewer.

Four dials tune it. Each takes a bare number of **seconds**, or a number with an `s` / `m` / `h` suffix — `900`, `15m`,
and `0.25h` all mean the same thing. A value that cannot be read falls back to its default rather than producing a cycle
of no length.

| Param                      | Default | Controls                                                                                                                                                                |
| -------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `demo_swap_chunk_interval` | `2m`    | One whole cycle — the board dwell plus the artifact tour — before swapping chunks.                                                                                      |
| `demo_board_scroll`        | `60s`   | How long the board's detail dock takes to scroll to its bottom. Clamped to **half** `demo_swap_chunk_interval`, so the artifact tour always keeps a share of the cycle. |
| `demo_artifact_interval`   | `20s`   | How long each artifact holds the screen, and so how long its scroll takes.                                                                                              |
| `demo_reload_after`        | `1h`    | Reload the page once it has been up this long. `0` disables it.                                                                                                         |

Two behaviors exist for the wall-screen case specifically, and are worth knowing before you blame the display:

- **The screen is held awake** via the Screen Wake Lock API, re-acquired whenever the tab becomes visible again (the
  browser drops the lock every time it is hidden). It needs a **secure context**, so a hub reached over plain HTTP falls
  back to the display's own idle timer. This is the usual reason a kiosk still blanks.
- **A redeploy is picked up automatically.** A single-page app fetched once otherwise runs its original bundle forever.
  The tour re-reads `index.html` past the HTTP cache at each chunk swap and reloads when the deployed document has
  changed; `demo_reload_after` is a backstop under that for what no deploy fixes. Both reloads happen **between**
  chunks, never mid-scroll, and the demo params ride the URL, so what comes back up is the tour rather than a plain
  board.

Demo mode needs the same session any board does — it drives real reads, so it will not run for a signed-out or
permissionless viewer (see [Human authentication](#human-authentication-oauth-login)). It only reads: it scrolls and
navigates, and never activates an operator control, so a board left touring cannot pause a runner or answer an ask.

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
pause** (issue #46; see "Four verbs, two axes" above). A pause the runner has already parked on locally is not marked at
all and simply stays parked, ADVANCE lifting it when the pause clears; a pause recorded only at the hub is discovered by
RESUME's own re-attach read, which re-parks the lease instead of respawning it. Either way the pause fact, not the
restart, decides. An ungraceful `kill -9` skips the *shutdown* marking, but not the resume: the next start marks the
sessions the crash orphaned before the loop begins (`marked N crash-interrupted lease(s) for restart-resume`), so the
same RESUME re-attaches them. What it costs is precision, not the context.

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
