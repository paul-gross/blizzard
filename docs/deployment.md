# Deployment and boot recovery

How a colocated blizzard machine — one hub and one supervisor (runner) side by side — is installed under systemd, and
the contract that makes it survive a crash or a reboot with nothing lost and nothing worked twice. This is the operator
reference for the following journey:

> At some point in the night the machine rebooted. It didn't matter: the supervisor and the colocated hub came back
> under systemd, the supervisor reaped the stale leases, re-read the environment bindings from its store, and continued
> — every chunk still at exactly the node the hub last recorded.

The two units live in [`packaging/systemd/`](../packaging/systemd/):
[`blizzard-hub.service`](../packaging/systemd/blizzard-hub.service) and
[`blizzard-runner.service`](../packaging/systemd/blizzard-runner.service).

## The colocated topology

One machine runs both daemons. Colocation is a choice, not a constraint — a runner on another machine points at the hub
the same way, and [`docs/remote-runner.md`](./remote-runner.md) walks that shape. Side by side they are two
personalities of the one `blizzard` wheel, so there is no version skew between them and no Node at install or runtime:

- **hub** — `blizzard-hub host`: the fleet's HTTP API, SSE, and the embedded mission-control board. Holds the forge base
  URL and work-source credentials — those live only here, never on the runner.
- **supervisor (runner)** — `blizzard-runner host`: the stateless `REAP → PULL → FILL → ADVANCE` loop behind a
  machine-local API. Reaches the hub outbound-only, so it keeps working while the hub is briefly unreachable — every
  such call carries the runner's enrolled bearer token (see [`deployment/runner-auth.md`](./deployment/runner-auth.md)).

Each daemon owns its own embedded store; neither opens the other's.

## Routing

Everything past the topology lives under [`deployment/`](./deployment/), one file per concern. Each is a single owner; a
fact stated in one is linked from the others, never restated.

### Standing the machine up

| File                                              | Read when…                                                                                                                                           |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`install.md`](./deployment/install.md)           | …installing the wheel, seeding each daemon's runtime directory, dropping the units — and the config renames and migration notes an upgrade owes.     |
| [`work-sources.md`](./deployment/work-sources.md) | …declaring the `[[work_source]]` bindings a chunk's work item is read through: credentials, label projection, delivery closure, ingest tokens.       |
| [`runner-auth.md`](./deployment/runner-auth.md)   | …enrolling a runner and rolling the fleet from `warn` to `enforce` — machine identity, not human login.                                              |
| [`human-auth.md`](./deployment/human-auth.md)     | …putting operators behind SSO: the `[auth]` table, the superuser bootstrap, roles, runner-side federation, and what a TLS-terminating proxy changes. |

### Configuring what workers do

| File                                              | Read when…                                                                                                                                 |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| [`worker-spawn.md`](./deployment/worker-spawn.md) | …deciding what a worker process is handed: forwarded environment vars, model and effort tiers, session stickiness, and the spawn preamble. |
| [`artifacts.md`](./deployment/artifacts.md)       | …authoring a graph's `produces:` or `artifacts:` keys, or flipping `produces_mode` to `enforce`.                                           |
| [`spend.md`](./deployment/spend.md)               | …bounding what an unattended fleet costs — the per-chunk cap, the rolling runner ceiling, and the subscription rate-limit read.            |

### Operating a running fleet

| File                                                      | Read when…                                                                                                                                 |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| [`runner-doors.md`](./deployment/runner-doors.md)         | …reaching a runner daemon — which of its two listeners a client addresses, and what each one will and won't do.                            |
| [`control-verbs.md`](./deployment/control-verbs.md)       | …stopping, re-aiming, or settling work: `chunk pause`/`restart`/`stop`/`done`, `detach`, and the two unrelated senses of "pause a runner". |
| [`chunk-operations.md`](./deployment/chunk-operations.md) | …taking over a parked session, editing an unclaimed chunk, migrating one to another graph, following the latest mint, or retiring a graph. |

### Watching it

| File                                                | Read when…                                                                                                              |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| [`observability.md`](./deployment/observability.md) | …a chunk is stuck and its status won't say why — the operational event log, and the kiosk board for a wall screen.      |
| [`transcripts.md`](./deployment/transcripts.md)     | …turning on either transcript lane: the context warn lane, or shipping session content to the hub. Both off by default. |
| [`analytics.md`](./deployment/analytics.md)         | …querying the event stream derived from shipped transcripts, or the duration/spend/outcome datasets built on it.        |
| [`recovery.md`](./deployment/recovery.md)           | …you need to know what survives a `kill -9` or a reboot, and how to prove it on your own machine.                       |
