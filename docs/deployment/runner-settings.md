# Runner settings

Each identified runner has its own settings record in the runner store. On every start, explicitly authored runner
values in the host file seed keys that have no stored entry. Thus a key newly added to the file can be seeded on a later
start; omitted keys resolve to defaults. Existing entries, including an explicit unset, are not overwritten by later
file edits or restarts. Use the settings API or CLI to change stored settings. Host settings, including the hub address
and authentication, remain file-owned and require a restart. Runner identity is immutable.

Without an explicit host `[tools]` table, the host's implicit tools profile is derived from the runners' **file** values
on every boot, even when stored runner worker values differ. Set `[tools]` explicitly to avoid coupling host children to
those file values. Worker sessions use the stored runner settings. See [worker environments](./worker-spawn.md) for the
two profiles.

## Lifetimes and status

A pass holds one coherent snapshot: a change made during a tick applies on a later tick, not halfway through the current
one. New invocations use their launch settings; already running work retains its own settings and harness set. Changing
a setting that selects a harness configuration bundle can rebuild the set, but editing the bundle's contents itself
requires a restart. A failed live rebuild leaves the last good context in use and is retried.

The settings view reports the record `revision`, each key's resolved `value`, `source` (`entry` or `default`),
`lifetime`, and available write provenance. `in_force.revision` and `since` identify the snapshot actually in use;
`in_force.failed` identifies a failed revision, error and time. A newer stored revision does not by itself mean a pass
has applied it. Older lease spawns can have no recorded launch revision.

## Operate

Select a runner by id or name with `--runner` when the host has more than one. The CLI reaches the running daemon via
its runtime directory or `--runner-url` and prints JSON:

```bash
blizzard runner settings get --dir /var/lib/blizzard --runner <id>
blizzard runner settings get agents --dir /var/lib/blizzard --runner <id>
blizzard runner settings set agents 2 --dir /var/lib/blizzard --runner <id>
blizzard runner settings set throttle.budget.usd 25.0 --dir /var/lib/blizzard --runner <id>
blizzard runner settings unset agents --dir /var/lib/blizzard --runner <id>
blizzard runner settings changes --dir /var/lib/blizzard --runner <id>
```

`set` takes a TOML literal; quote strings, lists, or inline tables for the shell. Whole lists and tables are replaced,
not merged. `unset` restores the default and remains authoritative over the file. The CLI reads the revision before
writing; if another writer wins, reread and retry deliberately.

Human clients can read `GET /runners/{runner_id}/api/settings` and `GET /runners/{runner_id}/api/settings/changes`; the
latter accepts `limit` and `before` and returns `next_before` for pagination. A superuser can send
`PATCH /runners/{runner_id}/api/settings` with `If-Match` set to the observed revision and a JSON body such as
`{"set":{"agents":2},"unset":[]}`. Missing or stale revisions, invalid values, unknown or non-writable keys, and
insufficient authority are refused; a refusal does not apply a partial edit. Worker credentials cannot access these
settings. Local Unix-socket operator access has implicit authority.

## Retirement and recovery

Retiring a runner can leave its identity alongside retained settings and history. An unconfigured retained identity
still triggers the wrong-store guard: stop the owned host and run
`blizzard runner store reset <host-directory> --runner <retired-runner-id>` to archive that identity and its scoped rows
together before restarting configured siblings. This is cleanup, not automatic retirement-aware resolution or a way to
restore a retired runner by archive ingest. See
[runner retirement and reset](./runner-auth.md#adding-and-retiring-a-hosts-runners).
