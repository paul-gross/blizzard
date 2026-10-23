# Installation

## First install

1. Create a system service account named `blizzard` with home `/var/lib/blizzard`, the shared state root the units
   declare as `StateDirectory`.
2. Install a release into a dedicated node-free virtualenv at `/opt/blizzard/venv`, the path the packaged units'
   `ExecStart` expects, from that release's hashed requirements file — the only install path; there is no package index.
   Name the version, fetch the file from the release page, check where it came from, and sync the venv to it
   ([verifying.md](../verifying.md) owns the checks):

   ```bash
   v=0.2.0-rc.2
   curl -fsSLO "https://github.com/paul-gross/blizzard/releases/download/v$v/blizzard-$v-requirements.txt"
   gh attestation verify "blizzard-$v-requirements.txt" --repo paul-gross/blizzard
   uv venv /opt/blizzard/venv --python 3.12
   uv pip sync --python /opt/blizzard/venv --require-hashes "blizzard-$v-requirements.txt"
   ```

   The file's last line names that release's own wheel by its page address and hash, and every dependency comes at the
   version and hash the release gate proved, so the one attestation check vouches for the whole install. Without `uv`:

   ```bash
   python3 -m venv /opt/blizzard/venv
   /opt/blizzard/venv/bin/pip install --require-hashes -r "blizzard-$v-requirements.txt"
   ```

   A hub whose store is Postgres and runs under systemd takes `blizzard-$v-requirements-pg.txt` instead. A runner
   machine takes the same file, at a version [versioning.md](../versioning.md#the-hubrunner-skew-window) allows against
   its hub. If the venv lives anywhere else, edit the units' `ExecStart` and `ExecStartPre` to the real binary path —
   systemd requires an absolute path there.
3. Seed the hub's runtime directory once, as the service account: `blizzard-hub init /var/lib/blizzard/hub` writes the
   config scaffold, the data directory, and a store migrated to head, and is idempotent.
4. Install the units by copying [packaging/systemd/blizzard-hub.service](../../packaging/systemd/blizzard-hub.service)
   and [blizzard-runner.service](../../packaging/systemd/blizzard-runner.service) into `/etc/systemd/system`, then
   `systemctl daemon-reload` and `systemctl enable --now blizzard-hub`; `enable` is what starts it at boot.
5. With the hub answering, seed the runner's runtime directory as the service account:
   `blizzard-runner init
   /var/lib/blizzard/runner` scaffolds it the same way, then adds the runner at the hub its
   config names and writes the runner's token to `/var/lib/blizzard/runner/.env`. Against a hub that requires sign-in,
   first run `blizzard hub
   runner add runner-local` as an operator — the name a fresh init scaffolds — and put the
   `BZ_HUB_TOKEN_RUNNER_LOCAL=…` line it prints in that `.env`, created owner-only (`0600`) as the service account
   ([remote-runner.md](../remote-runner.md#add)); init then keeps the token
   ([runner-auth.md](./runner-auth.md#adding-a-runner)). Re-running init keeps the runner while the hub still knows the
   token. Then `systemctl enable --now blizzard-runner`.

## Configuration and credentials

The hub's forge credentials are stored secrets, not environment variables: its work sources are records owned by
[work-sources.md](./work-sources.md), the repositories it delivers to are records owned by
[repositories.md](./repositories.md), and each names a secret owned by [secrets.md](./secrets.md) — create them with
`blizzard hub secret`, `source` and `repo` once the hub answers. `/etc/blizzard/hub.env` carries deployment values only.
The runner's workspace and harness bindings live in its host directory's `blizzard-host.toml` and carry no credentials.

The hub's deployment-varying values — db_url, host, port — also resolve from the environment at load, precedence CLI
flag over env var over toml over default: `BZ_HUB_DB_URL` (no flag exists), `BZ_HUB_HOST`, `BZ_HUB_PORT`, with
`--host`/`--port` on `hub host` only; `hub host` and `hub migrate` resolve identically through `HubConfig.load`. All
override variables unset leaves the resolved config byte-identical to a toml-only load; a malformed `BZ_HUB_PORT` fails
with a `ConfigError` naming the variable, from `hub init` and from every later load alike.

`action_pass_seconds` (default 300) in `blizzard-hub.toml` bounds one pass of a hub node's action, and the environment
does not override it. A pass that overruns it is cancelled at its next wait, answers `pending` where its action can wait
and its failure choice where it cannot, and records `hub-action-overran` ([observability.md](./observability.md)); the
hub lets go of a pass still stuck thirty seconds later, so it never holds the hub's slot past that.

## Runtime directories

Every verb that takes a runtime directory resolves it from three rungs, highest first: the explicit flag or argument,
then the daemon's environment variable, then the current working directory. `BZ_HUB_DIR` names the hub runtime dir
(`blizzard-hub.toml` plus `data/hub.db`); `BZ_RUNNER_DIR` names the runner's host directory (`blizzard-host.toml` plus
`data/host.db` and `host.sock`). The packaged units pass `--dir` explicitly; the variables exist for callers that cannot
write a flag per invocation. Which runtime-dir flags and positionals each verb takes is that verb's own `--help`
contract.

Selectable is not shareable: the store is single-writer and each daemon migrates on boot, so aiming a second live daemon
at a runtime dir a running instance holds risks lock contention and corruption — the variable chooses a root, it does
not make one safe to share.

## Upgrades

To adopt a new release, fetch and verify its requirements file as in step 2 of [First install](#first-install), run
`uv pip sync` (or `pip install --require-hashes -r`) against it into the same venv, and `systemctl restart` both units.
`uv pip sync` leaves the venv holding exactly that file's set, so no dependency lingers from the version before. Going
back is the same with the earlier version's file, subject to the store's own rules ([rollback.md](../rollback.md)).
There is no manual migration step: each unit's `ExecStartPre` runs `migrate` before the daemon opens its store, and the
daemon refuses to start on a revision mismatch, so a forgotten migration fails loudly instead of corrupting state. A
graceful `systemctl restart` preserves in-flight work across an upgrade; [recovery.md](./recovery.md) owns that
contract.

`migrate` reads `blizzard-hub.toml` before touching the store, so a config the new wheel rejects fails `ExecStartPre`
and the unit never starts; make any required config edit in the same maintenance window as the wheel, before the
restart. The migrate-on-start safety story covers additive or backfill schema revisions only — not a destructive
revision whose `upgrade()` deletes rows, and not a config change the new wheel requires.

### The table-dropping migration

The `20260929_1100_drop_open_pr_facts` migration is the first in either store whose `upgrade()` drops tables, rows
included (`20260716_2206_pr_opened_idempotent` is the earliest to delete rows, only the duplicate `delivery_pr_opened`
rows its unique constraint rejects): it drops the retired `delivery_pr_opened` and `delivery_pr_closed` tables;
`downgrade()` recreates both empty and never restores the rows. No delivery path writes either table any more, so a
store the current wheel produced holds none, but the drop is unconditional and irreversible — stop the hub, then copy
its store file (sqlite `hub.db` with any `hub.db-wal` beside it, or the postgres equivalent) before restarting into a
wheel carrying it; the revision-mismatch guard cannot catch it afterward. Restore the pair as one unit — `hub.db` and
its `-wal` sidecar together — and remove any `hub.db-wal` already at the destination first, or its stale frames replay
over the restored file.

### Work sources and forge settings are records

Work sources, repositories and their credentials live in the hub's store. A hub refuses to start while its
`blizzard-hub.toml` still declares `[[work_source]]` blocks or its environment still sets `BZ_FORGE_URL`,
`BZ_FORGE_TOKEN`, `BZ_FORGE_OWNER`, `BZ_FORGE_BASE_BRANCH`, or a variable a block's `token_env` names. The refusal names
the remedy: before the import has run, the import command; after it, each key that remains and where it was found.

Carry a file-configured hub across with the hub stopped, on the hub host, with its old environment still set:

1. Run `blizzard hub migrate --dir <hub-dir>`.
2. Run `blizzard hub config import-legacy --dir <hub-dir>`. It creates a secret per credential, a work source record per
   block, and a repository record per repo the hub has delivered commits to, in one transaction, and lists what it
   created. It writes into the hub's only tenant; a hub holding several refuses unless `--tenant <tenant_id>` names one.
   A record the tenant already holds is skipped, never overwritten; a second run writes nothing.
3. Delete the `[[work_source]]` blocks and unset the variables.
4. Start the hub.

A hosted or config-as-code deployment can skip the import: store each credential with `blizzard hub secret create`, then
declare the work sources and repositories in a [configuration document](./config-documents.md) and run
`blizzard hub config apply`, removing the keys before the restart.

Response bodies carry no rename alias: `pm_pointers` is now `work_refs` on every chunk, queue, and envelope view; a
client reading the old name gets an empty list, not an error, so client code must change whichever path it calls.
`GET /chunks/{id}/pm-items` remains a deprecated alias for `/work-items` on both daemons — a courtesy for out-of-tree
callers, not supported forever; move to `/work-items`.

## Graph sync after a deploy

Run `blizzard hub graph sync` after every deploy, once the hub is back up: the hub resolves a minted graph per chunk
from its store and never re-reads packaged YAML, so a deploy shipping a changed graph and stopping at the restart leaves
every new chunk on the previous definition, with nothing in errors, logs, or status saying so.

`graph sync` covers every tenant in one call: the hub reconciles its packaged graphs into each open tenant, each through
that tenant's own store, and reports per tenant. It is a hub administrator's verb and takes no `--tenant`. A tenant
created later needs no sync of its own — creation mints the packaged graphs into it.

`graph sync` compares each packaged graph's fully inlined definition against the newest mint of its name and mints only
what differs, so it is safe to run unconditionally — an unchanged wheel mints nothing and churns no lineage. A mint
folds every file a graph references into the stored definition, so an edit confined to a referenced file that never
touches `graph.yaml` is a real graph change a `graph.yaml` diff misses; `blizzard hub graph mint --help` enumerates
which keys carry file references — diff all of them before calling a deploy complete.

A packaged graph that fails to load or validate reports failed and exits `graph sync` non-zero without stopping the
others — or the other tenants — from reconciling. Confirm a sync with `blizzard hub graph list --tenant <tenant_id>` —
the newest mint per name should read effective.

Minting is additive: the new definition becomes effective for future resolution while every in-flight chunk stays pinned
to the definition it started under; moving one deliberately is `hub chunk migrate`, owned by
[chunk-operations/migration.md](./chunk-operations/migration.md).
