# Runner settings

Each identified runner has its own settings record in the runner store, and the host has one more for its own live keys.
On every start, explicitly authored live settings in the host file seed keys that have no stored entry. Thus a key newly
added to the file can be seeded on a later start; omitted keys resolve to defaults. Existing entries, including an
explicit unset, are not overwritten by later file edits or restarts. Use the settings API or CLI to change stored
settings. Boot-only settings — the host's, such as the hub address and authentication, and each runner's — remain
file-owned and require a restart. Runner identity is immutable.

## The host's settings

The host's record holds its top-level live keys: `tick_seconds`, `transcripts.ship`, `transcripts.record_max_bytes`,
`transcripts.chunk_max_bytes`, `worker_stdout.retention_days`, `tracing.sweep_seconds`, `tracing.settle_seconds`,
`tracing.batch_limit`, `tracing.max_lag_seconds`, `tracing.replay_max_window`, `tracing.worker_programs`,
`tracing.worker_program_services`, `tracing.harness_telemetry`, `tools.path_prepend` and `tools.env_passthrough`. A
table value such as `tracing.worker_program_services` is one whole entry. Each key's lifetime is in the settings view: a
`pass` key reaches the next runner tick, host pass or trace sweep, and a `session` key — the `tools.*` keys and the
three worker tracing keys — reaches the next worker spawn, tool child or worker export, never one already running.

The host seeds its record at start, before any runner is built, in one write. Beyond the file's authored keys:

- `tick_seconds` falls through to the deprecated `BZ_RUNNER_TICK_SECONDS` only where neither the store nor the file
  states it; the variable's value is then seeded. A set variable warns at start, saying whether it was seeded or
  ignored.
- Without a `[tools]` table, the seed captures the profile every runner declares alike as the `tools.*` entries, when
  that profile is not empty. Once the store holds a `tools.*` entry, the host no longer refuses runners whose file
  profiles disagree, and later file edits to the runners' worker values do not move the host's profile. See
  [worker environments](./worker-spawn.md) for the two profiles. Worker sessions use the stored runner settings.

A write that moves `tick_seconds` or `tracing.sweep_seconds` wakes every timed driver to re-read its interval: one whose
new due time, counted from the end of its last pass, has passed runs at once, and the others wait out the rest. A cut
from an hour to a second therefore takes hold without waiting the hour.

## Lifetimes and status

A pass holds one coherent snapshot: a change made during a tick applies on a later tick, not halfway through the current
one. New invocations use their launch settings; already running work retains its own settings and harness set. Changing
a setting that selects a harness configuration bundle can rebuild the set. For a published bundle, its source is
fingerprinted each pass, so an in-place content change can also trigger a rebuild without a restart. A failed live
rebuild leaves the last good context in use and is retried.

The settings view reports the record `revision`, each key's resolved `value`, `source` (`entry` or `default`),
`lifetime`, and available write provenance. `in_force.revision` and `since` identify the snapshot actually in use;
`in_force.failed` identifies a failed revision, error and time. A newer stored revision does not by itself mean a pass
has applied it. Older lease spawns can have no recorded launch revision. If the settings cannot be resolved, `settings`
is empty and `settings_error` explains why; the record revision and `in_force` status remain available. No resolved
values are substituted from an older revision.

## Operate

Select a runner by id or name with `--runner` when the host has more than one. The CLI reaches the running daemon via
its runtime directory or `--runner-url` and prints JSON:

```bash
blizzard runner settings get --dir /var/lib/blizzard --runner <id>
blizzard runner settings get agents --dir /var/lib/blizzard --runner <id>
blizzard runner settings set agents 2 --dir /var/lib/blizzard --runner <id>
blizzard runner settings set throttle.budget.usd 25.0 --dir /var/lib/blizzard --runner <id>
blizzard runner settings unset runner_prompt --dir /var/lib/blizzard --runner <id>
blizzard runner settings changes --dir /var/lib/blizzard --runner <id>
```

`set` takes a TOML literal; quote strings, lists, or inline tables for the shell. Whole lists and tables are replaced,
not merged. `unset` restores the default and remains authoritative over the file; it is refused if the resulting
configuration is invalid, for example when a runner with project bindings has no `agents`. The CLI reads the revision
before writing; if another writer wins, reread and retry deliberately.

Human clients can read `GET /runners/{runner_id}/api/settings` and `GET /runners/{runner_id}/api/settings/changes`; the
latter accepts `limit` and `before` and returns `next_before` for pagination. A superuser can send
`PATCH /runners/{runner_id}/api/settings` with `If-Match` set to the observed revision and a JSON body such as
`{"set":{"agents":2},"unset":[]}`. Missing or stale revisions, invalid values, unknown or non-writable keys, and
insufficient authority are refused; a refusal does not apply a partial edit. Worker credentials cannot access these
settings. Local Unix-socket operator access has implicit authority.

Add `--host` to any `settings` verb to address the host's own record instead of a runner's; it takes no runner selector,
and naming `--runner` beside it is refused:

```bash
blizzard runner settings get --host --dir /var/lib/blizzard
blizzard runner settings set tick_seconds 10 --host --dir /var/lib/blizzard
blizzard runner settings changes --host --dir /var/lib/blizzard
```

The host's routes are `GET` and `PATCH /api/host/settings` and `GET /api/host/settings/changes`, with the same
authority, `If-Match` and refusals as a runner's. Naming a boot key such as `port` is refused `setting_not_live`, and an
edit that leaves the host's file invalid is refused `settings_invalid`, naming every failing key. Each change row also
carries `record_kind` — `host` for the host's own entries — and `record_key`, and the changes route filters on both.

### Apply and export a document

The host's settings, its workspaces and subscriptions, and its runners' settings can also be kept as one document shaped
like `blizzard-host.toml`: its host-level live keys, and its `[[runner]]`, `[[workspace]]` and `[[subscription]]`
tables. Export it, preview the change, then apply it:

```bash
blizzard runner settings export --dir /var/lib/blizzard > host-settings.toml
blizzard runner settings apply host-settings.toml --dry-run --dir /var/lib/blizzard
blizzard runner settings apply host-settings.toml --dir /var/lib/blizzard
```

Neither verb takes `--host` or `--runner`: the document is the host's. A `[[runner]]` table names a runner the host
holds by its `id`; a `[[workspace]]` names its record by `id` and a `[[subscription]]` by `slug`. An entry sets only the
keys it states, and a record the document leaves out is never touched or retired. A stated workspace or subscription the
host does not hold is created, and a retired one is enabled. Apply never unsets a key; use `settings unset` for that.
Adding or retiring a runner is not an apply's: see
[runner retirement and reset](./runner-auth.md#adding-and-retiring-a-hosts-runners).

The apply is one transaction, and any refusal fails the whole document and writes nothing. It refuses a runner id the
host does not hold (`runner_unknown`), a boot key (`setting_not_live`), an undeclared key (`setting_unknown`), an
immutable key that differs from the record's (`setting_immutable`), and a malformed document or a value that leaves the
host's file invalid (`settings_invalid`). Each refusal names the entry by `record_kind` and `record_key`. Every change
row an apply writes carries door `apply` and one `apply_id`; `--dry-run` answers the same outcomes, with a `null`
`apply_id`, and writes nothing.

Export states every entry not at its default: the host's, each hosted runner's under its `id`, and every active
workspace and subscription whole. It is not a backup of boot settings. Applying an export back answers every outcome
`unchanged` and writes no change row.

Over HTTP a superuser sends `POST /api/host/settings/apply` with the document as a JSON object, and `?dry_run=true` to
preview it; the answer lists one outcome per host key, per stated record and per stated runner. Human clients read
`GET /api/host/settings/export`.

## Retirement and recovery

Retiring a runner can leave its identity alongside retained settings and history. An unconfigured retained identity
still triggers the wrong-store guard: stop the owned host and run
`blizzard runner store reset <host-directory> --runner <retired-runner-id>` to archive that identity and its scoped rows
before restarting configured siblings; its outbox counters stay in the original store. This is cleanup, not automatic
retirement-aware resolution or a way to restore a retired runner by archive ingest. See
[runner retirement and reset](./runner-auth.md#adding-and-retiring-a-hosts-runners).
