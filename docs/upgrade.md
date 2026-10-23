# Upgrade

Upgrading the hub is restart-based — pull a new image tag, recreate the container — with no rolling, hot-swap, or
draining path. The brief outage while the new container migrates and starts is the whole cost. What follows is the
reference compose deployment ([`docs/install.md`](./install.md)).

## Before you pull

If `init`, `migrate`, `host`, or `runner ingest` says a store is below this build's migration baseline, install the
floor release named in the error and migrate that store with that release first. Then reinstall the target release and
rerun the command. The refusal leaves the store's revision untouched; a normal pending-revision mismatch instead names
the current build's migrate command.

Read the release's **Upgrade notes** and act on whatever it asks:

```bash
gh release view v0.2.0 --repo paul-gross/blizzard
```

[`docs/versioning.md`](./versioning.md) owns what counts as breaking and the floor a runner may lag its hub down to.

## Upgrading to the next release

### Usage-limit brake carry-over

A usage limit no longer engages a runner's brake; it holds the subscription's usage-limit floor, which opens on its own
([`docs/deployment/spend.md`](./deployment/spend.md#usage-limits)). Upgrading does not clear a brake a usage limit
engaged before it: such a runner stays braked, its reason still reading `usage limit: …`, until you run
`blizzard runner start --runner <id>` for it. From then on the floor holds it while its subscription is exhausted, and
its parked leases resume when the floor opens.

### Caps count estimated spend

Both `[runner.cost]` caps now trip on billed plus estimated spend
([`docs/deployment/spend.md`](./deployment/spend.md#the-two-caps)), where they read billed spend alone before. A cap set
against billed spend only can now trip early on a runner that spends a subscription. Before upgrading, review each
runner's `runner_ceiling_usd` and `chunk_cap_usd` against its recent `~`-marked spend in `blizzard hub status`, and
raise any cap that recent figure would cross.

### Runner-only tracing keys in the hub's configuration

`worker_programs`, `worker_program_services` and `harness_telemetry` configure the runner's receiver; the hub never read
them. If `blizzard-hub.toml`'s `[tracing]` table carries any of them, delete them, or move them to `blizzard-host.toml`
where the runner reads them. The hub now refuses to start with one present, and its error names `blizzard-host.toml`.

## Upgrading to 0.2.0

`0.2.0` is reached from `v0.1.0-rc.1` or any `edge` build, stepping through no intermediate release, since none exists.
Every boundary below therefore applies at once, and one sequence satisfies all of them: let the chunks in flight finish,
stop every runner, and take a hub store backup ([`docs/backup.md`](./backup.md)); install the `0.2.0` runner software on
every runner, leaving each stopped; upgrade the hub, which migrates on boot; then start the runners. The runners are
upgraded before the hub deploys, as the subscription-usage boundary needs, and none runs against the hub until it is on
`0.2.0`, as the runner-identity boundary needs.

### Subscription-usage response boundary

One release removes `RunnerView.external_subscription_usage` — the singular usage response — leaving the per-slug
`subscriptions` collection as the only subscription surface. The release that does it marks the removal under **Breaking
changes**, and its **Upgrade notes** name the hub version that drops the field and the minimum runner version that
reports per-subscription samples; read those before pulling, because the two facts below are what make this boundary
safe to cross.

**Order.** Upgrade every runner to slug-carrying, per-subscription reporting **before** deploying the hub, because the
hub rejects a slug-less sample rather than defaulting it; a runner left behind is not supported against the upgraded
hub. API clients must read `subscriptions`; the removed field is gone from `openapi/hub.openapi.json` and the generated
TypeScript client in the same release.

**If a runner is still pre-boundary.** Its usage samples carry no `slug`, and the hub **rejects** them — it does not
silently discard them. Each rejected sample is named in the batch ack, and the runner logs `hub rejected buffered fact`
at error severity once per sample interval. That log is the signal, not a new fault: the drain acks and moves on rather
than wedging, so nothing backs up and no other traffic is affected. The runner's subscription block stays empty on the
board for as long as it lasts.

**Getting back.** The condition clears on its own the moment that runner is upgraded — there is no hub-side cleanup and
no data to repair. Confirm the runner is reporting again with `blizzard runner external-usage probe <slug>`
([`docs/deployment/spend.md`](./deployment/spend.md)), then check that the subscription reappears on the runner's board
panel.

**A separate case.** A sample that *does* carry a slug but whose windows are unusable is accepted, not rejected: the bad
windows are dropped at intake and the subscription renders **present with no windows**, which is not the same as a
pre-boundary runner's absent block. Both are logged at warning severity on the hub with the runner and slug.

**`sampled_at` can be null.** `SubscriptionUsageView.sampled_at` is now nullable: for a miss-only subscription row — one
with no successful sample, whose credential has lapsed — it is `null`, and `condition` carries the reason. This is
deliberate and counts as breaking on the HTTP API, so an API client that reads `sampled_at` as always present must
handle null and read `condition` in that case.

### File-configured work sources boundary

A hub crossing into store-held work sources refuses to start while its `blizzard-hub.toml` keeps `[[work_source]]`
blocks or its container environment keeps `BZ_FORGE_*` variables. Carry them into records before recreating the
container — [`docs/deployment/install.md`](./deployment/install.md#work-sources-and-forge-settings-are-records) owns the
step.

### Work-source identity boundary

A hub crossing into work sources keyed by id migrates each source's name to a slug and a display name, and rewrites
every reference to the source, chunks' work refs included, to its id. A work ref naming no source of its tenant is left
as stored and logged, and can never be reattached afterwards; create the missing sources, and carry any
`[[work_source]]` blocks into records, before pulling.
[`docs/deployment/work-sources.md`](./deployment/work-sources.md#upgrading-a-hub-with-existing-external-chunks) owns the
check. A configuration document applied afterwards keys each work source by `slug`; one keyed by `name` alone is refused
`422`.

### Capability-reporting boundary

A runner that reports no capabilities is eligible for nothing. This is breaking on the hub↔runner wire, because the
empty `capabilities` default used to mean "no filter".

**Order.** Upgrade every runner to capability-reporting **before** deploying the hub.

**If a runner is still pre-boundary.** It registers, but the matched peek never returns it an entry and every claim it
makes is refused with a `409` naming it as `incompatible_runner_id`.

**Getting back.** The condition clears the moment that runner is upgraded; there is nothing to repair on the hub.

### Runner identity boundary

One release keys every runner by an id the hub mints, and retires `runner_auth_mode`. From that release on:

- **Every runner authenticates.** A fleet call without a token the hub issued is refused with a `401` under every
  configuration. A `blizzard-hub.toml` that still sets `runner_auth_mode` boots with the key ignored; delete it whenever
  convenient.
- **Ids are hub-minted.** The hub's migration gives every runner an `rn_` id and keeps its old id as its name. Each
  enrolled token resolves to its runner's new id, so no runner re-enrolls. Operator verbs and `/api/runners/{id}` take
  the `rn_` id, which `hub runner list` shows beside each name, so a script that passed an old id passes the new one.
- **A runner that held no token** — one a `warn` hub admitted — keeps its history under its new id, but is refused until
  `blizzard hub runner enroll <id>` mints its token. Put the line `enroll` prints in the runner's runtime-dir `.env`.
- **The name is the runner's own.** A pre-host `blizzard-runner.toml` that declares a `runner_id` and no `name` reads
  that value as the name, and [graduation](#host-graduation-boundary) carries it into the host file's `name`; a runner
  renames itself by changing `name` and restarting ([remote-runner.md](./remote-runner.md#point-it-at-the-hub)).
- **`runner init` adds the runner** at the hub its config names and writes its token
  ([runner-auth.md](./deployment/runner-auth.md#adding-a-runner)). Anything that re-runs init on a runner directory that
  outlives its hub's data passes `--allow-readd`.
- **Exported and traced runner ids change.** The egress `steps` and `invocations` rows and the `blizzard.runner.id` span
  attribute carry the `rn_` id from the upgrade on, each with the runner's name beside it.
  [egress.md](./deployment/egress.md#upgrading-to-hub-minted-runner-ids) owns re-exporting history under the new ids,
  and [tracing.md](./deployment/tracing.md#upgrading-to-hub-minted-runner-ids) owns querying across the upgrade.

**Order.** Across this boundary a runner may not run against the hub until it has been upgraded: the hub takes every
runner's identity from its bearer token and answers with `rn_` ids that a runner of the previous release cannot follow.
Let the chunks in flight finish, stop every runner, and take a hub store backup ([`docs/backup.md`](./backup.md)).
Upgrade each runner while it is stopped: its store migration runs offline before the daemon starts, as the packaged
unit's `ExecStartPre` runs it. Upgrade the hub, which migrates on boot. Then start the runners; each registers with the
token it already holds and keeps the id the hub minted for it.

**If a runner is still pre-boundary.** It is not supported against the upgraded hub; keep it stopped until it is
upgraded.

**Getting back.** A rollback across this boundary is lossy for runner ids;
[`docs/rollback.md`](./rollback.md#what-a-rollback-does-not-undo) names what does not come back.

### Tenants boundary

One release holds the hub's fleets as tenants ([tenants.md](./deployment/tenants.md)). From that release on:

- **The store migrates into one tenant.** The hub's migration creates the tenant `default` and carries every existing
  row into it. Each person's role becomes a membership there: `guest`, `contributor` and `admin` keep theirs, the
  claimed superuser becomes an `admin` member and a hub administrator, and a `pending` person gets no membership.
  Sessions survive, so nobody signs in again.
- **Sign-in is invite-only.** Existing people keep their memberships and sign in as before. A person the hub does not
  know needs an invitation, and the configured `[auth].superuser` still signs in uninvited
  ([human-auth.md](./deployment/human-auth.md#who-may-sign-in)). Issue invitations for anyone you expect to join.
- **Roles are managed per tenant.** `blizzard hub member` and `/api/members` manage a tenant's members; `GET /api/users`
  and `POST /api/users/{id}/role` still answer, over the request's tenant's members only.
- **Exports are a hub administrator's.** Egress and trace status, backfill, reset and replay take `export:admin`, which
  no tenant role holds. Egress rows gain `tenant_id` and `tenant_name`
  ([egress.md](./deployment/egress.md#upgrading-to-tenants)).
- **Runners need nothing.** The hub↔runner wire change is additive, so the usual skew window holds: each runner keeps
  its id and token, serves `default`, and learns its tenant from its next registration.

**Order.** Take a hub store backup ([`docs/backup.md`](./backup.md)), then upgrade the hub, which migrates on boot.

**Getting back.** The migration's downgrade refuses while the hub holds more than one tenant, or while a tenant's
deletion is underway, naming them; a rollback is possible only from a hub that holds `default` alone.

### Host graduation boundary

One release makes every runner directory a host directory: it reads `blizzard-host.toml`, keeps its store at
`data/host.db`, and keeps each runner's token under a variable of its own. The release refuses a directory that holds
`blizzard-runner.toml` and no `blizzard-host.toml`. `blizzard runner host`, `migrate` and `init` all refuse it, naming
this page, and change nothing, so a standard upgrade pass that reaches an unconverted directory stops before restarting
anything.

**Order.** Graduate every runner directory on the machine together, in one session, with the release installed and the
runners stopped: runners sharing one installation cannot graduate one at a time, since the release they share refuses
whichever is left.

1. **Rehearse.** Install the new release apart from the one the runners run, in a separate virtualenv, and call that
   venv's `blizzard`. Build the copy from parts, never by copying the directory: a fresh `<copy>/data` and the runner's
   host file and `.env`, leaving behind the live `data/` (its store's `-wal`/`-shm`, which SQLite could replay into the
   copy) and `runner.sock`. Convert the copy's file by step 6, and drop from the copy's host file any `db_url` that
   names the live store, so `data/host.db` applies: a `db_url` kept there makes `migrate --dir <copy>` migrate the live
   store under the running daemon. Then take the store, which is still in use, with SQLite's backup API —
   `sqlite3 -readonly <dir>/data/runner.db ".backup '<copy>/data/runner.db'"` — never a file copy, which can miss pages
   still in the write-ahead log. The copy holds the converted `blizzard-host.toml` and the `.env` with the renamed token
   variable beside the store, since `host --check` reads both. A token held outside `.env` (a unit's `Environment=`, the
   launching shell) must be exported as `BZ_HUB_TOKEN_<NAME>` in the shell that runs the copy's `--check`, or written
   into the copy's `.env`. Run `blizzard runner migrate --dir <copy>` and `blizzard runner host --check --dir <copy>` on
   the copy; the check resolves the token at the live hub, a read that registers nothing and changes nothing. The copy
   is only for `migrate` and `--check`: never run `host` on it, which would register a second host under the live
   runner's id. A failure costs nothing.
2. **Drain.** Pause every runner at the hub (`blizzard hub runner pause <runner_id> --hub-url <hub>`, adding
   `--tenant <tenant_id>` when you belong to several tenants), so nothing new is claimed, and let any chunk already
   claimed reach the end of its current node. Close every open takeover, whose session holds an address the restart
   retires. Work in the middle of a node needs no draining; the restart resumes it.
3. **Stop** every runner.
4. **Install** the new release where the runners run. Do it after the stop and before the migrate: a runner restarted
   under the new release refuses the unconverted directory, so the new release belongs on the machine only once nothing
   is left to restart.
5. **Back up** each directory's `data/runner.db` with its `-wal` and `-shm` siblings, its `blizzard-runner.toml`, and
   its `.env` to a dated directory.
6. **Convert the file.** The runner's directory becomes the host directory, and `blizzard-runner.toml` becomes
   `blizzard-host.toml`:
   - The host keys stay at the top: `hub_url`, `host`, `port`, `public_url`, `trusted_proxies`, `session_secret_env`,
     `[auth]`, `[tracing]`, `[transcripts]` and `[worker_stdout]`.
   - `workspace_*`, `base_branch`, `max_environments` and the workspace prompt keys become one `[[workspace]]`;
     `base_branch` and `max_environments` keep their names there: `workspace_id` is its `id` (`workspace-local` when the
     old file set none), `workspace_root` its `root`, `workspace_provider` its `provider`, `workspace_envs` its `envs`,
     `workspace_prompt` its `prompt`, `workspace_prompt_file` its `prompt_file`, `workspace_prompt_package` its
     `prompt_package`, and each `[[workspace_repo]]` a `[[workspace.repo]]`.
   - Everything else becomes one `[[runner]]`; the keys the host file refuses at the top level for that reason are
     `name`, `projects`, `gates`, `runner_prompt`, `runner_prompt_file`, `transcripts_root`, `harness`, `claude_code`,
     `opencode`, `models`, `effort`, `cost`, `queue`, `context`, `worker`, `harness_binary`, `harness_permission_mode`,
     `worker_settings_path` and `claude_code_credentials_path`. Its `name` is the old `name`, or the legacy `runner_id`
     key, which held a name, or `runner-local` when the file had neither. Its `id` is the hub id the store records as
     its identity, which `blizzard hub runner list` shows beside the name. Read it from the store: first
     `sqlite3 -readonly <dir>/data/runner.db "select distinct runner_id from runner_identity"`; more than one result
     means the store names several runners, so stop and go back by **Getting back**. Then
     `sqlite3 -readonly <dir>/data/runner.db "select runner_id from runner_identity order by id desc limit 1"` prints
     the newest row's id. Step 8's check refuses an `id` other than the one the store names. Its `gates`,
     `runner_prompt`, `runner_prompt_file`, `harness_binary`, `harness_permission_mode`, `[harness]`, `[claude_code]`,
     `[opencode]`, `[models]`, `[effort]`, `[worker]`, `[queue]`, `[context]`, `[cost]`, `transcripts_root`,
     `worker_settings_path` and `claude_code_credentials_path` move under it, each path keeping its value. A moved
     table's header takes the `runner.` prefix: `[opencode]` becomes `[runner.opencode]`, `[opencode.models.aliases]`
     becomes `[runner.opencode.models.aliases]`.
   - Its `projects` are bindings of the form `{ project = "<slug>", workspace = "<workspace id>", agents = <n> }`, one
     per project it served: the old `projects` list, or, where the file had none, each project the hub last answered it
     serves, which the store records in `hub_served_projects`:
     `sqlite3 -readonly <dir>/data/runner.db "select
     position, entry, slug, project_id from hub_served_projects order by position"`
     lists them, and each row's `slug` becomes a binding's `project`. A row with a NULL `slug` is an entry no project
     answered to: use its `project_id` when it has one, and otherwise drop it, since a binding naming no project is
     refused. **Split the old `max_agents` across the bindings, at least 1 agent each.** Each binding's `agents` caps
     that binding alone, so an old cap below the number of served projects graduates higher than it was, unless you drop
     projects.
   - Its `subscription` names its one `[[subscription]]` declaration. A file with several declarations moves every one
     to the host and names the plan its enabled harness spends. A file with none sampled a plan synthesized from
     `[external_subscription_usage]`; declare it as `[[subscription]]` with `slug = "anthropic"`, `name = "Anthropic"`,
     `provider = "anthropic"` and the old table's `sample_interval_seconds` and `credentials_path`, if it set them, and
     name it. A declaration without `slug`, `name` or `provider` is refused.
   - `db_url` is optional at the host level, and defaults to `data/host.db`. Drop a `db_url` that names the
     `data/runner.db` of the directory the file sits in, whether by a relative or an absolute path, so the default
     applies; this holds for the live directory and for a copy alike. Keep a `db_url` only for a store elsewhere on
     purpose.
   - `token_env` is dropped; the host file refuses it, as it refuses `runner_id`, `workspace_*`, `max_agents` and
     `[external_subscription_usage]`, naming where each belongs.
   - Rename the token variable `BZ_HUB_TOKEN` (or the one `token_env` named) to `BZ_HUB_TOKEN_<NAME>`, where `<NAME>` is
     the runner's name upper-cased with every character outside `A-Z0-9` replaced by `_`: `r-claude` reads
     `BZ_HUB_TOKEN_R_CLAUDE`. Rename it wherever the old one was set: `.env`, a unit's `Environment=` or
     `EnvironmentFile`, or the shell that launches the runner. A token may instead sit in the host file's `[hub_tokens]`
     table, keyed by the runner's name.
   - Place the tables in order: top-level keys first; each `[runner.*]` table after its `[[runner]]` and before the next
     `[[...]]`; each `[[workspace.repo]]` after its `[[workspace]]`. TOML assigns a key to the nearest table header
     above it, so a key placed after a header belongs to that table.
   - Rename the old file to `blizzard-runner.toml.graduated`.
7. **Migrate the store.** `blizzard runner migrate --dir <dir>` folds `data/runner.db`'s write-ahead log into it and
   moves it, with its siblings, to `data/host.db`, then applies the pending revisions. It adopts only when the host file
   configures no other store, no `data/host.db` exists, and no pre-host daemon answers on the directory's `runner.sock`;
   it holds the directory's lock for the move. A host store that already exists is migrated in place. Run `migrate`
   first: every other verb refuses a directory whose pre-host store is not yet adopted, naming `migrate`, and creates no
   `data/host.db`. An empty `data/host.db` beside `data/runner.db` is refused by `migrate`; remove the empty file and
   re-run. A `data/host.db` that holds tables beside it is refused too: decide which file is the runner's store and move
   the other aside by hand, since `migrate` will not choose.
8. **Check.** `blizzard runner host --check --dir <dir>` loads the host file, verifies the store's revision, and
   resolves each runner's token to its `id` at the hub, starting nothing. It must pass before anything starts. A refusal
   that the store belongs to another runner than the configured one means the `[[runner]] id` is wrong: it prints the id
   to correct it to, which is the one step 6 reads from the store; when it lists more than one runner, go back by
   **Getting back** whatever id it prints. A store that names several runners, the configured one among them, is refused
   untouched with the instruction to stop and restore the right store: a host store holds one, so go back by **Getting
   back** rather than force the check. The check does not cover prompt files, prompt packages or the harness config
   bundle: they resolve only at start, so a missing one first shows there (step 9). During graduation never run
   `blizzard runner store reset`, which sets the store aside and starts the runner on an empty one, abandoning its
   leases and backlog.
9. **Start** each host with `blizzard runner host --dir <dir>` (or the packaged unit that runs it), confirm
   `blizzard runner status --dir <dir>` names each runner and its hub id, and resume each runner at the hub
   (`blizzard hub runner resume <runner_id> --hub-url <hub>`, adding `--tenant <tenant_id>` when you belong to several
   tenants). A `warning: runner <name> (<id>) is halted: …` line at start means that runner did not start: stop the host
   and return to step 8. A host that exits at start with a config error names the key or file it could not resolve, a
   prompt file, a prompt package or the harness config bundle among them: fix that key in `blizzard-host.toml`, or go
   back by **Getting back**.

The result is an unregistered host of one runner, under the same id and token, with every chunk it held.

To gather several graduated hosts into one registered host, take each runner under it at the hub, then move it in with
`blizzard runner ingest` ([runner-auth.md](./deployment/runner-auth.md#ingesting-a-runner-from-another-store)).

**One directory per store.** `data/host.lock` and `host.sock` guard the host directory, not a store named by `db_url`;
`runner.sock` is only probed for a pre-host daemon left running, in the host directory and beside a configured store
that sits in another directory's `data`, where `migrate` and `host --check` refuse while one answers. A configured store
belongs to exactly one host directory; never point two at it.

**Getting back.** Stop the hosts, restore the backed-up files over the converted ones, remove `blizzard-host.toml` and
`data/host.db*`, reinstall the previous release, start the runners and resume them. Token renames made outside `.env` (a
unit's `Environment=` or `EnvironmentFile`, the launching shell) are not in the backup: back them up before the
conversion, or rename them back to the variable the previous release reads. The hub needs nothing undone, and this is
clean only before the runners resume work: after that, the hub has taken in leases, results and facts the restored store
never saw, so the store is older than the hub's view of the runner. Resume a runner on a restored store only when it has
done no work since the conversion; past that, its restored store cannot be trusted to match the hub, and the runner
should be re-added instead (`blizzard runner init --allow-readd`).

## Pull and recreate

From `packaging/docker/`, pull the tag:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:0.2.0
```

Set `BLIZZARD_HUB_IMAGE` to that tag in `.env`, then recreate the hub alone, leaving postgres and Caddy untouched:

```bash
docker compose up -d hub
```

Recreation is what migrates: the entrypoint migrates the store to the new head before serving, and the daemon refuses to
start on a revision mismatch.

Confirm the new version is serving:

```bash
curl -s https://<your-domain>/api/health | jq .version
curl -s https://<your-domain>/api/ready
```

A failed migration exits the container with the reason in `docker compose logs hub`, leaving the store unchanged because
migrations run in a transaction; the previous tag stays pullable ([`docs/rollback.md`](./rollback.md)).

## Why the restart is safe while runners are mid-work

A runner reaches the hub outbound-only and never assumes it is reachable — that is what makes the restart safe.

A runner's usage, event, and completion traffic is store-and-forward: it keeps working and buffers while the hub is
down, then flushes once the hub answers, losing nothing and double-sending nothing because replay is idempotent. The
intentional exception is a pre-boundary slug-less subscription sample, which the hub rejects rather than stores — the
runner acks it and moves on instead of wedging the queue, so the rest of the buffer still flushes. Upgrade runners
before the hub; see the subscription-usage response boundary above.

A chunk a runner is mid-node on stays claimed across the outage, and the runner's recovery pass — the same one that
survives a `kill -9` ([`docs/deployment.md`](./deployment.md#the-colocated-topology)) — re-reads state when the hub
returns instead of assuming the work was lost.

A worker's hub-proxied read — the runner-local routes that forward to the hub on a worker's behalf — rides the restart
out too: the forward retries with bounded backoff instead of surfacing the restart window's `502` straight to the
worker, so a call landing mid-swap still answers once the hub comes back.
