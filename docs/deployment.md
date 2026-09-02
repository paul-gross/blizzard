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
stateless `REAP → PULL → FILL → ADVANCE` loop behind a machine-local API; it reaches the hub outbound-only with its
enrolled bearer token ([`docs/deployment/runner-auth.md`](./deployment/runner-auth.md)), so it keeps working while the
hub is briefly unreachable. Each daemon owns its own embedded store; neither opens the other's.

The units are [`packaging/systemd/`](../packaging/systemd/)'s `blizzard-hub.service` and `blizzard-runner.service`;
under them both daemons survive a crash or reboot with nothing lost and nothing worked twice.

## Operator concerns

### Standing the machine up

| File                                                       | When to read                                                                                                                                                |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/install.md`](./deployment/install.md)         | You are installing the wheel, seeding each daemon's runtime directory, and dropping the units — plus the config renames and migration notes an upgrade owes |
| [`deployment/runner-auth.md`](./deployment/runner-auth.md) | You are enrolling a runner and rolling the fleet from `warn` to `enforce` — machine identity, not human login                                               |
| [`deployment/human-auth.md`](./deployment/human-auth.md)   | You are putting operators behind SSO: the `[auth]` table, the superuser bootstrap, roles, runner-side federation, and what a TLS-terminating proxy changes  |

### Configuring what workers do

| File                                                                             | When to read                                                                                                                                         |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/work-sources.md`](./deployment/work-sources.md)                     | You are declaring the `[[work_source]]` bindings a chunk's work item is read through: credentials, label projection, delivery closure, ingest tokens |
| [`deployment/worker-spawn.md`](./deployment/worker-spawn.md)                     | You are deciding what a worker process is handed: forwarded environment vars, model and effort tiers, session stickiness, and the spawn preamble     |
| [`deployment/artifacts.md`](./deployment/artifacts.md)                           | You are authoring a graph's `produces:` or `artifacts:` keys, or flipping `produces_mode` to `enforce`                                               |
| [`deployment/transcripts.md`](./deployment/transcripts.md)                       | You are turning on either transcript lane — the context warn lane, or shipping session content to the hub; both off by default                       |
| [`deployment/routines-and-scopes.md`](./deployment/routines-and-scopes.md)       | You are authoring a routine's graph and run defaults, or a scope's slug and description                                                              |
| [`deployment/findings-and-proposals.md`](./deployment/findings-and-proposals.md) | You are reading a routine's findings bucket, or listing the garden proposals waiting on a decision                                                   |

### Operating a running fleet

| File                                                                 | When to read                                                                                                                                                                                         |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`deployment/runner-doors.md`](./deployment/runner-doors.md)         | You are reaching a runner daemon: which of its two listeners a client addresses, and what each one will and won't do                                                                                 |
| [`deployment/chunk-operations.md`](./deployment/chunk-operations.md) | You are taking over a parked session, editing an unclaimed chunk, migrating one to another graph, following the latest mint, retiring a graph, or declaring or releasing a dependency between chunks |
| [`deployment/control-verbs.md`](./deployment/control-verbs.md)       | You are stopping, re-aiming, or settling work: `chunk pause`/`restart`/`stop`/`done`, `detach`, and the two unrelated senses of "pause a runner"                                                     |
| [`deployment/spend.md`](./deployment/spend.md)                       | You are bounding what an unattended fleet costs: the per-chunk cap, the rolling runner ceiling, and the subscription rate-limit read                                                                 |
| [`deployment/recovery.md`](./deployment/recovery.md)                 | You need to know what survives a `kill -9` or a reboot, and how to prove it on your own machine                                                                                                      |

### Diagnostics

| File                                                                             | When to read                                                 |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| [`deployment/opencode-compatibility.md`](./deployment/opencode-compatibility.md) | You are running the pinned OpenCode compatibility diagnostic |

### Watching it

| File                                                           | When to read                                                                                                           |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [`deployment/observability.md`](./deployment/observability.md) | A chunk is stuck and its status won't say why: the operational event log, and the kiosk board for a wall screen        |
| [`deployment/analytics.md`](./deployment/analytics.md)         | You are querying the event stream derived from shipped transcripts, or the duration/spend/outcome datasets built on it |
