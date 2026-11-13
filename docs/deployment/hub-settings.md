# Hub settings

Hub settings govern the whole hub, independently of any tenant. Hub administrators can inspect and change live
operational settings without restarting the hub. These are not the tenant's `blizzard hub settings` (which names its
default urgency), nor the tenant configuration document ([configuration documents](./config-documents.md)).

## Inspect and edit

```bash
blizzard hub admin settings get
blizzard hub admin settings get auth.login_throttle.capacity
blizzard hub admin settings set auth.login_throttle.capacity 20
blizzard hub admin settings unset auth.login_throttle.capacity
blizzard hub admin settings changes --all
```

`set` takes a TOML value: quote a string as a TOML string inside shell quotes, for example
`blizzard hub admin settings set egress.datasets '["steps", "invocations"]'`. `unset` returns a key to its declared
default; it does not return authority to the file. The listing shows each live key's effective value, source (`entry` or
`default`) and lifetime. `--json` also exposes the settings revision and write provenance. Invalid values and unknown or
non-live keys are refused. Writes validate the resulting configuration together, and a no-op records no change.

The supported live keys are the `pass` lifetime keys in the
[hub configuration contract](../../contracts/config/hub.json). A request or sweep pass reads a consistent settings
snapshot; a change takes effect on subsequent work, not midway through a pass. Only `pass` keys are stored. Boot
settings are not exposed by this interface: edit the file, environment or flags as appropriate and restart the hub to
apply them. Session keys are not exposed either. For live keys, the file seeds an absent entry, not an override of an
existing one; see [upgrade](../upgrade.md#hub-settings-boundary) for migration and rollback.

## Documents and history

```bash
blizzard hub admin settings export > hub-settings.toml
blizzard hub admin settings apply hub-settings.toml --dry-run
blizzard hub admin settings apply hub-settings.toml
```

The document is TOML with the same nested keys as the hub configuration, but may contain only live settings. Apply is
atomic and sets only the keys present; it never unsets omitted keys. Export contains explicitly set live values,
including values equal to their declared defaults; it omits absent and unset entries. It is not a complete replacement
or a backup of boot settings: applying it to another hub leaves that hub's omitted values untouched. Use `unset`
explicitly to reset a key. The hub settings change log is separate from the tenant configuration log; it records
changes, their actor, door and revision, and groups an apply by its apply id.

Over HTTP a hub administrator uses `GET` and `PATCH /api/admin/settings`, `GET /api/admin/settings/changes`,
`POST /api/admin/settings/apply` and `GET /api/admin/settings/export`. PATCH accepts `set` and `unset` and requires an
`If-Match` revision; missing or stale revisions are refused. Apply accepts a JSON object and `?dry_run=true` previews
it. The CLI reads the current revision before a set or unset. Runner credentials cannot use these routes.
