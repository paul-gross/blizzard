# Installing the colocated hub and runner

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
#    (init scaffolds a commented-out example — see deployment/work-sources.md);
#    the runner's workspace/harness bindings live in its own blizzard-runner.toml,
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
[`hub chunk migrate`](./chunk-operations.md#migrating-a-claimed-chunk-to-another-graph)). Confirm with
`blizzard hub graph list` — the newest per name should read `effective`.

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
would parse as *zero* configured external work sources — a hub that boots clean while every board label for an external
pointer renders null (the built-in `hub` source, issue #357, is unaffected either way — it needs no `[[work_source]]`
entry at all). So `HubConfig.load` raises instead, naming the new key.

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
