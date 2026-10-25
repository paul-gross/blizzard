# Flags

A **flag** is a switch a release ships beside a capability that is not yet finished, so the work can land on `master`
and ride the weekly train dark. Each flag is declared in blizzard's registry, `foundation/flags/registry.toml`, with a
one-line summary of the capability, and starts off. A flag's scope says whose answer counts: `hub`, `runner`, or
`fleet`, where the hub's answer is carried to every runner.

A flag is turned on by a release, not by you: the release that turns a capability on says so in its notes, under the
flag's summary. A flag lasts about one stable release on, then is retired and its name never returns.

## Surface behind an off flag is already released

A flag changes what blizzard does with a surface, never whether the surface exists. A route, field, CLI verb, or
configuration key behind an off flag is in the release, held to [versioning](./versioning.md) like any other: a client
written against it keeps working the morning the flag turns on. Used while off, a route answers `404` with a body naming
the flag, a CLI verb exits 1 saying so, and a configuration key is read as absent with a warning.

## Overrides

An override is a `[flags]` table in `blizzard-hub.toml` or `blizzard-host.toml`, with flag names as keys and `true` or
`false` as values:

```toml
[flags]
toolkit-dialogs = true
```

An override is for trying unfinished work early, on an instance you can afford to experiment on. It carries no support
promise: the behavior may change or vanish before it turns on, and a bug in it is not a regression. It is never per
tenant — one process, one answer — and is read once at start, so changing one is a restart. A key naming a flag of
another scope, a retired flag, or no flag is ignored with a warning in the log. A `fleet` flag is the hub's alone to
answer; set it in `blizzard-hub.toml`.

The `[flags]` table is not a configuration key under [versioning](./versioning.md): adding or removing a flag never
changes the configuration surface.

## Where the marker shows

A process that overrides any flag says so. The board shows a quiet marker beside the hub's version, a runner's panel
shows the same for its host, and `GET /api/health` on the hub lists the overridden names. `GET /api/flags` answers every
live flag of the hub's scopes and whether it is on.
