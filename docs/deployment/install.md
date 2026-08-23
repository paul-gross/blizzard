# Installation

## First install

1. Create a system service account named `blizzard` with home `/var/lib/blizzard`, the shared state root the units
   declare as `StateDirectory`.
2. Install the single blizzard wheel into a dedicated node-free virtualenv at `/opt/blizzard/venv`, the path the
   packaged units' `ExecStart` expects. If the wheel lives anywhere else, edit the units' `ExecStart` and `ExecStartPre`
   to the real binary path — systemd requires an absolute path there.
3. Seed each daemon's runtime directory once, as the service account: `blizzard-hub init /var/lib/blizzard/hub` and
   `blizzard-runner init /var/lib/blizzard/runner`; `init` writes the config scaffold, the data directory, and a store
   migrated to head, and is idempotent.
4. Install the units by copying [packaging/systemd/blizzard-hub.service](../../packaging/systemd/blizzard-hub.service)
   and [blizzard-runner.service](../../packaging/systemd/blizzard-runner.service) into `/etc/systemd/system`, then
   `systemctl daemon-reload` and `systemctl enable --now` on both; `enable` is what starts them at boot.

## Configuration and credentials

The hub's delivery credentials (`BZ_FORGE_URL`, `BZ_FORGE_TOKEN`) go in `/etc/blizzard/hub.env`; its work sources are
`[[work_source]]` blocks in `blizzard-hub.toml`, owned by [work-sources.md](./work-sources.md); the runner's workspace
and harness bindings live in its own `blizzard-runner.toml` and carry no credentials.

The hub's deployment-varying values — db_url, host, port — also resolve from the environment at load, precedence CLI
flag over env var over toml over default: `BZ_HUB_DB_URL` (no flag exists), `BZ_HUB_HOST`, `BZ_HUB_PORT`, with
`--host`/`--port` on `hub host` only; `hub host` and `hub migrate` resolve identically through `HubConfig.load`. All
override variables unset leaves the resolved config byte-identical to a toml-only load; a malformed `BZ_HUB_PORT` fails
with a `ConfigError` naming the variable, from `hub init` and from every later load alike.

## Runtime directories

Every verb that takes a runtime directory resolves it from three rungs, highest first: the explicit flag or argument,
then the daemon's environment variable, then the current working directory. `BZ_HUB_DIR` names the hub runtime dir
(`blizzard-hub.toml` plus `data/hub.db`); `BZ_RUNNER_DIR` names the runner runtime dir (`blizzard-runner.toml` plus
`data/runner.db` and `runner.sock`). The packaged units pass `--dir` explicitly; the variables exist for callers that
cannot write a flag per invocation. Which runtime-dir flags and positionals each verb takes is that verb's own `--help`
contract.

Selectable is not shareable: the store is single-writer and each daemon migrates on boot, so aiming a second live daemon
at a runtime dir a running instance holds risks lock contention and corruption — the variable chooses a root, it does
not make one safe to share.

## Upgrades

To adopt a new wheel, `pip install` it into the venv and `systemctl restart` both units — no manual migration step: each
unit's `ExecStartPre` runs `migrate` before the daemon opens its store, and the daemon refuses to start on a revision
mismatch, so a forgotten migration fails loudly instead of corrupting state. A graceful `systemctl restart` preserves
in-flight work across an upgrade; [recovery.md](./recovery.md) owns that contract.

`migrate` reads `blizzard-hub.toml` before touching the store, so a config the new wheel rejects fails `ExecStartPre`
and the unit never starts; make any required config edit in the same maintenance window as the wheel, before the
restart. The migrate-on-start safety story covers additive or backfill schema revisions only — not a destructive
revision whose `upgrade()` deletes rows, and not a config change the new wheel requires.

### The one destructive migration

The `20260716_2206_hub_pr_opened_idempotent` migration is the first in either store whose `upgrade()` deletes rows: it
deletes every `delivery_pr_opened` row but the earliest per (chunk_id, repo) before adding a unique constraint there;
`downgrade()` only drops the constraint and never restores the rows. That delete removes only true duplicates of a
forge-deduplicated `pr.opened` fact, but it is unconditional and irreversible — copy the hub's store file (sqlite
`hub.db`, or the postgres equivalent) before restarting into a wheel carrying it; the revision-mismatch guard cannot
catch it afterward.

### The `[[work_source]]` rename

A hub whose `blizzard-hub.toml` still declares `[[pm_source]]` will not start on this wheel: the key is renamed
`[[work_source]]` with the block's contents unchanged, and `HubConfig.load` raises naming the new key — under the
systemd layout `ExecStartPre`'s migrate is what fails, so the daemon never comes up; edit the toml before the restart.
The refusal is deliberate rather than a silent alias: an ignored `[[pm_source]]` block would parse as zero external work
sources, booting the hub clean while every external pointer's board label renders null; the built-in hub source needs no
entry and is unaffected. `token_env` values need no change: only the table key was renamed — the scaffold's example
value changed, but the variable name is your own choice.

Response bodies carry no rename alias: `pm_pointers` is now `work_refs` on every chunk, queue, and envelope view; a
client reading the old name gets an empty list, not an error, so client code must change whichever path it calls.
`GET /chunks/{id}/pm-items` remains a deprecated alias for `/work-items` on both daemons — a courtesy for out-of-tree
callers, not supported forever; move to `/work-items`.

## Graph sync after a deploy

Run `blizzard hub graph sync` after every deploy, once the hub is back up: the hub resolves a minted graph per chunk
from its store and never re-reads packaged YAML, so a deploy shipping a changed graph and stopping at the restart leaves
every new chunk on the previous definition, with nothing in errors, logs, or status saying so.

`graph sync` compares each packaged graph's fully inlined definition against the newest mint of its name and mints only
what differs, so it is safe to run unconditionally — an unchanged wheel mints nothing and churns no lineage. A mint
folds every file a graph references into the stored definition, so an edit confined to a referenced file that never
touches `graph.yaml` is a real graph change a `graph.yaml` diff misses; `blizzard hub graph mint --help` enumerates
which keys carry file references — diff all of them before calling a deploy complete.

A packaged graph that fails to load or validate reports failed and exits `graph sync` non-zero without stopping the
others from reconciling. Confirm a sync with `blizzard hub graph list` — the newest mint per name should read effective.

Minting is additive: the new definition becomes effective for future resolution while every in-flight chunk stays pinned
to the definition it started under; moving one deliberately is `hub chunk migrate`, owned by
[chunk-operations/migration.md](./chunk-operations/migration.md).
