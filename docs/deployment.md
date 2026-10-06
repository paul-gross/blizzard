# Deployment

This document owns the **colocated topology** — hub and supervisor (runner) side by side on one machine under systemd —
and routes every other operator concern to single-owner leaves under `docs/deployment/`: a fact lives in one leaf and is
linked, never restated.

## The colocated topology

Hub and runner are two personalities of the one `blizzard` wheel — no version skew between them, no Node at install or
runtime. Colocation is a choice, not a constraint — a runner on another machine points at the hub the same way
([`docs/remote-runner.md`](./remote-runner.md)).

The hub, `blizzard-hub host`, serves the fleet's HTTP API, SSE, and the embedded mission-control board, and alone holds
the forge base URL and work-source credentials — never the runner. The supervisor, `blizzard-runner host`, is the
stateless `REAP → PULL → FILL → ADVANCE` loop behind a machine-local API; it reaches the hub outbound-only with the
bearer token the hub issued when it added the runner ([`docs/deployment/runner-auth.md`](./deployment/runner-auth.md)),
so it keeps working while the hub is briefly unreachable. Each daemon owns its own embedded store; neither opens the
other's.

The units are [`packaging/systemd/`](../packaging/systemd/)'s `blizzard-hub.service` and `blizzard-runner.service`;
under them both daemons survive a crash or reboot with nothing lost and nothing worked twice.

Exactly one hub process serves a store at a time — never two `blizzard-hub host` instances, load-balanced or otherwise,
against the same database. blizzard-context's
[`architecture/system-shape/exclusive-writes.md`](https://github.com/paul-gross/blizzard-context/blob/master/architecture/system-shape/exclusive-writes.md)
owns what that assumption still costs.

## Operator concerns

### Standing the machine up

| File                                                       | When to read                                                                                                                                                |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/install.md`](./deployment/install.md)         | You are installing the wheel, seeding each daemon's runtime directory, and dropping the units — plus the config renames and migration notes an upgrade owes |
| [`deployment/runner-auth.md`](./deployment/runner-auth.md) | You are adding a runner, rotating or revoking its token, or re-running `runner init` — machine identity, not human login                                    |
| [`deployment/human-auth.md`](./deployment/human-auth.md)   | You are putting operators behind SSO: the `[auth]` table, the superuser bootstrap, roles, runner-side federation, and what a TLS-terminating proxy changes  |
| [`deployment/secrets.md`](./deployment/secrets.md)         | You are storing a credential in the hub, choosing where its encryption key lives, or rotating that key                                                      |

### Configuring what workers do

| File                                                                             | When to read                                                                                                                                     |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`deployment/config-changes.md`](./deployment/config-changes.md)                 | You are asking who changed a work source, a repository, or a secret, when, through which door, and what changed                                  |
| [`deployment/config-documents.md`](./deployment/config-documents.md)             | You are declaring work sources and repositories in one YAML or JSON file, applying it, dry-running it, or exporting the current records          |
| [`deployment/work-sources.md`](./deployment/work-sources.md)                     | You are creating the work source records a chunk's work item is read through: credentials, label projection, delivery closure, ingest tokens     |
| [`deployment/repositories.md`](./deployment/repositories.md)                     | You are storing the repositories work lands in: forge, owner, repo, base branch, and the secret that authenticates                               |
| [`deployment/worker-spawn.md`](./deployment/worker-spawn.md)                     | You are deciding what a worker process is handed: forwarded environment vars, model and effort tiers, session stickiness, and the spawn preamble |
| [`deployment/artifacts.md`](./deployment/artifacts.md)                           | You are authoring a graph's `produces:` or `artifacts:` keys, or flipping `produces_mode` to `enforce`                                           |
| [`deployment/transcripts.md`](./deployment/transcripts.md)                       | You are turning on either transcript lane — the context warn lane, or shipping session content to the hub; both off by default                   |
| [`deployment/routines-and-scopes.md`](./deployment/routines-and-scopes.md)       | You are authoring a routine's graph and run defaults, or a scope's slug and description                                                          |
| [`deployment/findings-and-proposals.md`](./deployment/findings-and-proposals.md) | You are reading a routine's findings bucket, or listing the garden proposals waiting on a decision                                               |

### Operating a running fleet

| File                                                                 | When to read                                                                                                                                                                                                                  |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/runner-doors.md`](./deployment/runner-doors.md)         | You are reaching a runner daemon: which of its two listeners a client addresses, and what each one will and won't do                                                                                                          |
| [`deployment/chunk-operations.md`](./deployment/chunk-operations.md) | You are taking over a parked session, editing an unclaimed chunk, migrating one to another graph, following the latest mint, retiring a graph, or declaring or releasing a dependency between chunks                          |
| [`deployment/control-verbs.md`](./deployment/control-verbs.md)       | You are stopping, re-aiming, or settling work: `chunk pause`/`restart`/`stop`/`done`, `detach`, and the two unrelated senses of "pause a runner"                                                                              |
| [`deployment/spend.md`](./deployment/spend.md)                       | You are bounding what an unattended fleet costs, or reading a figure it recorded: the per-chunk cap, the rolling runner ceiling, how a harness's reported cost becomes one invocation's, and the subscription rate-limit read |
| [`deployment/recovery.md`](./deployment/recovery.md)                 | You need to know what survives a `kill -9` or a reboot, and how to prove it on your own machine                                                                                                                               |
| [`deployment/egress.md`](./deployment/egress.md)                     | You are exporting the fleet's steps and invocations to files for a warehouse or DuckDB: turning it on, the data dictionary, loading whole passes, the newest-copy view, backfill and reset                                    |

### Diagnostics

| File                                                                             | When to read                                                                                                                                            |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/opencode-compatibility.md`](./deployment/opencode-compatibility.md) | You are running the OpenCode compatibility diagnostic against the runner's admitted version range, or admitting a new candidate version or corpus to it |

### Watching it

| File                                                           | When to read                                                                                                                                           |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`deployment/observability.md`](./deployment/observability.md) | A chunk is stuck and its status won't say why: the operational event log, the kiosk board for a wall screen, and a worker's raw captured stdout/stderr |
| [`deployment/analytics.md`](./deployment/analytics.md)         | You are querying the event stream derived from shipped transcripts, or the duration/spend/outcome datasets built on it                                 |
| [`deployment/tracing.md`](./deployment/tracing.md)             | You are building a backend or dashboard against a chunk's traces: the span shape, every attribute, id derivation, and an example collector fan-out     |
