# Tracing

Every step a chunk takes is told as one trace: a root span for the step, child spans for the waits inside or around it,
and links from each trace to the one before. The hub tells the root and its waits; a runner tells the lease it ran into
the same trace, under the root (see [Runner spans](#runner-spans)). This page describes the shape of that trace, so a
backend can be built against it and an operator can read one. The shape is a versioned contract —
[`contracts/traces/`](../../contracts/traces/README.md) pins it, and
[`docs/versioning.md`](../versioning.md#the-trace-contract) owns what may change.

## The shape of a trace

A **step** is one attempt at a node by a runner or the hub, or one human decision at a gate. Each closed step is its own
trace; an open step is not told until it closes. The root span is `step <node>` for an attempt and `gate <node>` for a
decision. It carries the step's dimensions, its token and cost totals and its wait totals, and its start and end are the
step's own.

Children hang off the root:

- the waits before a step — `queue wait`, then `claim` — start before the step does, so the root is not stretched to
  cover them. A chunk that waited a day for a runner reads as a day-long child beside an hour-long step.
- `ask`, `pause` and `hub exec` sit inside the step.
- a gate's only child is `decision pickup`, which starts as the gate ends.

Events on the root record each harness invocation (`invocation`), each time the hub polled a pending hub step
(`hub poll pending`) and a bounce (`bounce`). An event's time never falls after the span's end.

A step's root links to the previous step's root, with a `blizzard.link.reason` saying why this trace follows that one:

- `next` — The chunk moved on to the next node.
- `retry` — The previous step ended without moving the chunk, and this one is at the same node.
- `bounce` — The previous step ended in a bounce.
- `restart` — An operator restarted the chunk between the two steps.
- `migration` — The chunk moved to another graph between the two steps.

A step that ends by escalating has an `ERROR` status; every other span's status is unset.

## Spans

| Span                        | Role                | What it covers                                                                               |
| --------------------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| `step <node>`               | `step`              | One runner or hub attempt at a node, the root of its trace.                                  |
| `gate <node>`               | `gate`              | One human decision at a node, the root of its trace.                                         |
| `queue wait`                | `queue`             | The chunk waiting claimable, pauses excluded.                                                |
| `claim`                     | `claim`             | From the claim to the runner starting the step.                                              |
| `ask`                       | `ask`               | A question to a person, from asked to answered.                                              |
| `pause`                     | `pause`             | The fleet paused during the step.                                                            |
| `decision pickup`           | `pickup`            | From a person deciding a gate to the decision being picked up.                               |
| `hub exec`                  | `hub-exec`          | A hub node's run holding its execution slot.                                                 |
| `worker <node>`             | `runner/worker`     | One runner lease, from its minting to its close, parented to the hub's step root.            |
| `invoke_agent <session>`    | `runner/invocation` | One harness invocation in the lease; plain `invoke_agent` when the node declares no session. |
| `parked on ask`             | `runner/ask-park`   | The worker parked on a question until it resumed.                                            |
| `parked on pause`           | `runner/pause-park` | The worker parked by a fleet pause until it resumed.                                         |
| `provider overload backoff` | `runner/overload`   | The worker backing off after the provider reported overload.                                 |
| `takeover`                  | `runner/takeover`   | A person took the worker's session over, until they handed it back.                          |

## Attributes

Every child span carries the root's dimensions, so a backend can filter a wait by chunk, node or runner without joining
back to the root. Measures — tokens, cost and the wait totals — ride the root only among the hub's spans. The invocation
attributes ride the `invocation` event on the root and the runner's `invoke_agent` spans, and the link reason rides the
link. Each attribute names the roles it rides in [`dictionary.json`](../../contracts/traces/dictionary.json). Attributes
marked optional are absent rather than empty when there is nothing to say.

| Attribute                                  | Type       | Meaning                                                                                                                                           |
| ------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blizzard.chunk.id`                        | `string`   | The chunk the step belongs to.                                                                                                                    |
| `blizzard.chunk.work_refs`                 | `string[]` | The chunk's work items as source-native tokens, such as `acme#42`.                                                                                |
| `blizzard.graph.name`                      | `string`   | The name of the graph the chunk was on.                                                                                                           |
| `blizzard.graph.id`                        | `string`   | The id of that graph version.                                                                                                                     |
| `blizzard.node.name`                       | `string`   | The node the step was at.                                                                                                                         |
| `blizzard.node.id`                         | `string`   | The id of that node in its graph version.                                                                                                         |
| `blizzard.node.executor`                   | `string`   | Who ran the step: `runner`, `hub` or `human`.                                                                                                     |
| `blizzard.step.epoch`                      | `int`      | The attempt counter the step ran under.                                                                                                           |
| `blizzard.step.visit`                      | `int`      | How many times the chunk has arrived at this node, counting this one.                                                                             |
| `blizzard.step.outcome`                    | `string`   | How the step ended: `transitioned`, `gated`, `migrated`, `escalated`, `released`, `stopped`, `completed`, `superseded`, `decided` or `restarted`. |
| `blizzard.step.choice`                     | `string`   | The name of the choice that moved the chunk on.                                                                                                   |
| `blizzard.step.to_node.name`               | `string`   | The node the step moved the chunk to; `graph:<name>` for a move to another graph.                                                                 |
| `blizzard.step.preceded_by`                | `string`   | What sits between this step and the previous one: `restart`, `requeue` or `released-claim`.                                                       |
| `blizzard.runner.id`                       | `string`   | The runner that held the step or ran the lease.                                                                                                   |
| `blizzard.harness.id`                      | `string`   | The harness: on a step, the one its last invocation that recorded one ran under; on a runner span, the one the lease or invocation ran under.     |
| `blizzard.harness.version`                 | `string`   | The version of that harness.                                                                                                                      |
| `blizzard.step.models`                     | `string[]` | The distinct models the step's invocations used, in first-use order.                                                                              |
| `blizzard.bounce.cause`                    | `string`   | Why the chunk was bounced back from this step.                                                                                                    |
| `blizzard.ask.answered`                    | `bool`     | Whether a person had answered the question when the span ended.                                                                                   |
| `blizzard.clock_skew`                      | `bool`     | Set when the answer is stamped before the question, so the span is clamped to zero length.                                                        |
| `blizzard.link.reason`                     | `string`   | Why this trace follows the one it links to: `next`, `retry`, `bounce`, `restart` or `migration`.                                                  |
| `blizzard.step.input_tokens`               | `int`      | Input tokens the step's invocations used, not counting cache.                                                                                     |
| `blizzard.step.output_tokens`              | `int`      | Output tokens the step's invocations produced.                                                                                                    |
| `blizzard.step.cache_read_tokens`          | `int`      | Input tokens the step's invocations read from the cache.                                                                                          |
| `blizzard.step.cache_create_tokens`        | `int`      | Input tokens the step's invocations wrote to the cache.                                                                                           |
| `blizzard.step.cost.usd`                   | `double`   | The step's cost in US dollars, including any estimate.                                                                                            |
| `blizzard.step.cost.estimated`             | `bool`     | Whether part of the cost is a subscription estimate rather than a billed amount.                                                                  |
| `blizzard.step.cost.partial`               | `bool`     | Whether some invocation reported no cost, so the total is a floor.                                                                                |
| `blizzard.step.wait.queue_ms`              | `int`      | Milliseconds the chunk waited claimable before a runner or the hub took it.                                                                       |
| `blizzard.step.wait.claim_ms`              | `int`      | Milliseconds between the claim and the step starting.                                                                                             |
| `blizzard.step.wait.ask_ms`                | `int`      | Milliseconds the step spent waiting on a person's answer.                                                                                         |
| `blizzard.step.wait.pause_ms`              | `int`      | Milliseconds the step spent paused.                                                                                                               |
| `blizzard.step.wait.pickup_ms`             | `int`      | Milliseconds between a person deciding a gate and the decision being picked up.                                                                   |
| `blizzard.invocation.kind`                 | `string`   | What the invocation was: `spawn`, `resume` or `judge`; a nudge reads `resume`.                                                                    |
| `blizzard.invocation.input_tokens`         | `int`      | Input tokens, not counting cache.                                                                                                                 |
| `blizzard.invocation.output_tokens`        | `int`      | Output tokens.                                                                                                                                    |
| `blizzard.invocation.cache_read_tokens`    | `int`      | Input tokens read from the cache.                                                                                                                 |
| `blizzard.invocation.cache_create_tokens`  | `int`      | Input tokens written to the cache.                                                                                                                |
| `blizzard.invocation.cost.usd`             | `double`   | The invocation's cost in US dollars, including any estimate.                                                                                      |
| `blizzard.invocation.cost.estimated`       | `bool`     | Whether the cost is a subscription estimate.                                                                                                      |
| `gen_ai.response.model`                    | `string`   | The model that answered.                                                                                                                          |
| `gen_ai.usage.input_tokens`                | `int`      | All input tokens, cache reads and writes included.                                                                                                |
| `gen_ai.usage.output_tokens`               | `int`      | Output tokens.                                                                                                                                    |
| `gen_ai.usage.cache_read.input_tokens`     | `int`      | Input tokens read from the cache.                                                                                                                 |
| `gen_ai.usage.cache_creation.input_tokens` | `int`      | Input tokens written to the cache.                                                                                                                |
| `blizzard.lease.id`                        | `string`   | The runner lease the span belongs to.                                                                                                             |
| `blizzard.lease.close_reason`              | `string`   | Why the lease closed, such as `transitioned`; either escalation reads `escalated`.                                                                |
| `blizzard.session.name`                    | `string`   | The declared session the node ran in.                                                                                                             |
| `blizzard.model.resolved`                  | `string`   | The model the runner resolved the session's tier to.                                                                                              |
| `blizzard.effort.resolved`                 | `string`   | The effort the runner resolved the session's effort to.                                                                                           |
| `blizzard.invocation.nudge`                | `bool`     | Whether a nudge opened the invocation.                                                                                                            |
| `blizzard.invocation.generation`           | `int`      | The worker generation the invocation belongs to.                                                                                                  |
| `blizzard.invocation.end_source`           | `string`   | What ended the invocation: `session_end`, `next_invocation` or `lease_close`.                                                                     |
| `blizzard.overload.streak`                 | `int`      | The backoff's place in its streak of overloads, counting from 1.                                                                                  |
| `blizzard.context.tokens`                  | `int`      | The session's context size in tokens when sampled.                                                                                                |
| `blizzard.check.index`                     | `int`      | The check's position in the node's declared checks, counting from 1.                                                                              |
| `blizzard.check.passed`                    | `bool`     | Whether that check passed.                                                                                                                        |
| `blizzard.checks.passed`                   | `bool`     | Whether every check passed.                                                                                                                       |
| `blizzard.checks.count`                    | `int`      | How many checks ran.                                                                                                                              |
| `gen_ai.operation.name`                    | `string`   | Always `invoke_agent`.                                                                                                                            |
| `gen_ai.agent.name`                        | `string`   | The declared session the node ran in.                                                                                                             |
| `gen_ai.conversation.id`                   | `string`   | The harness session id, for a worker invocation.                                                                                                  |
| `gen_ai.request.model`                     | `string`   | The model the runner resolved for the session.                                                                                                    |

Cost is in US dollars. When a subscription estimate stands in for a billed amount, `blizzard.step.cost.estimated` says
so, and `blizzard.step.cost.partial` says some invocation reported no cost at all. `gen_ai.*` names follow the
OpenTelemetry GenAI semantic conventions at version `1.44.0`; their input-token total counts cache reads and writes,
where the `blizzard.invocation.*` counts do not.

## Resource attributes

The hub's spans are emitted under the instrumentation scope `blizzard.hub.fleet_spans` and a runner's under
`blizzard.runner.runner_spans`, both at version `1`. The resource carries:

| Attribute                       | Type     | Meaning                                                                                                                   |
| ------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------- |
| `service.name`                  | `string` | The service name; `blizzard-hub` or `blizzard-runner` unless `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` names one. |
| `service.version`               | `string` | The running hub's or runner's version.                                                                                    |
| `blizzard.trace.schema_version` | `string` | The version of this contract.                                                                                             |

`blizzard.trace.schema_version` is `1`. A breaking change to the shape raises it together with the scope version; a
backend that keys on either can tell shapes apart.

## Trace and span ids

Ids are derived, never random, so the same step always lands in the same trace and a backend can find a step's trace
without a lookup. A step's key is `<chunk id>/<epoch>` for an attempt and `<chunk id>/<epoch>/gate/<decision id>` for a
gate.

- The trace id is the first 16 bytes of the SHA-256 of `blizzard-trace/v1/` followed by the key.
- A span id is the first 8 bytes of the SHA-256 of `blizzard-span/v1/`, the key, `/`, the span's role and `/`, and a
  discriminator. The discriminator is the source row's own id for a role that can occur more than once in a step (`ask`,
  `pause`, `hub-exec`) and empty otherwise.
- A runner span's role is prefixed `runner/`, and its discriminator is the lease id for `runner/worker`,
  `<generation>/<kind>` for `runner/invocation`, and the source row's id for the other runner roles.
- An all-zero digest has its last byte set to `01`, so an id is never zero.
- Every span is sampled.

For attempt `ch_1/1`, the trace id is `914265ea3262e40441fa95402ba66e6d` and its root span id is `88d23b1ca9cc34d2`. The
full set of worked vectors is in [`dictionary.json`](../../contracts/traces/dictionary.json).

## What never leaves

No span carries prompt text, transcript content or check output; an ask's question or answer, or a gate's choice
descriptions; a bounce's envelope; the name or login of anyone who resolved a gate or answered an ask; or a chunk's
title or body. Work items, names, ids, counts, durations and costs are the whole of what a trace holds.

## Runner steps start at the claim

A runner step starts when the hub records the lease, not when the worker spawned. That gap is usually one runner drain,
but a runner that cannot reach the hub stores what it has and forwards it later, and then the gap can run to hours. The
step's root keeps the hub's start, so a runner step's start is biased early by that gap, and its `claim` span ends at
the same instant.

## Fanning one stream to two backends

[`packaging/otel-collector/collector.yaml`](../../packaging/otel-collector/collector.yaml) is an example OpenTelemetry
Collector configuration that receives OTLP over HTTP and sends every trace to two backends. It is validated against
`otelcol-contrib` 0.162.0 by `mise run collector-config-check`.

- **Receiver.** One `otlp` receiver on the HTTP protocol, port 4318 — protobuf over HTTP is the only protocol the hub
  will send.
- **Processor.** `batch` groups spans before export, so the backends see fewer, larger requests.
- **Exporters.** `otlp/store` sends over gRPC to a self-hosted store such as Tempo; `otlphttp/hosted` sends over HTTP
  with an API key header to a hosted service such as Honeycomb. Both feed from the one `traces` pipeline.
- **Credentials.** Endpoints and the API key are read from the environment (`TRACE_STORE_ENDPOINT`,
  `TRACE_STORE_INSECURE`, `HOSTED_TRACES_ENDPOINT`, `HOSTED_TRACES_API_KEY`), never written into the file.

Adjust the endpoints and the header name to your backends; keep the pipeline shape.

## Runner spans

A runner tells each lease it closes into the trace of the step the lease ran, under the instrumentation scope
`blizzard.runner.runner_spans` at version `1`. A `worker <node>` span covers the lease, with the lease's invocations,
parks, overload backoffs and takeovers as its children. The [Spans](#spans) and [Attributes](#attributes) tables list
them beside the hub's.

- **Turning them on.** The runner reads the same OpenTelemetry variables as the hub, and only those:
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` turns its sweep on, and a protocol other than
  `http/protobuf` is rejected. `service.name` is `blizzard-runner` unless `OTEL_SERVICE_NAME` or
  `OTEL_RESOURCE_ATTRIBUTES` names one. The sweep's knobs are the `[tracing]` block of `blizzard-runner.toml`, with the
  same names and defaults as the hub's; `batch_limit` counts closed leases.
- **Where it runs.** Only `blizzard runner host` sweeps, on a thread of its own, so a slow or hung backend never delays
  a tick. `blizzard runner tick` never tells anything. At shutdown the runner waits a few seconds for an export in
  flight, then exits without it; the lease is told again on the next start.
- **Events.** The runner reports `trace-export-failed`, `trace-export-recovered`, `trace-window-skipped` and
  `trace-config-rejected` the same way the hub does. They reach the hub's event log attributed to the runner, carrying
  no chunk or lease. A rejected setting is reported once each time the runner starts, and the runner keeps working with
  tracing off.

**Meeting the hub's step.** No message passes between the daemons. Both derive the step's trace id from the chunk and
epoch, and the `worker` span's parent is the span id the hub derives for that step's root, so a backend shows the
runner's spans under the hub's step. A runner span id uses the same derivation with its role prefixed `runner/`. If the
hub has tracing off, the runner's spans still group by trace id under a parent that never arrives.

**The gap between them.** The two daemons stamp times with their own clocks, and a runner mints its lease before the hub
hears of it, so a `worker` span can start before its parent. Neither side clamps to the other. The gap is the start bias
described under [Runner steps start at the claim](#runner-steps-start-at-the-claim), made visible; a gap that does not
match the runner's drain cadence points at clock skew between the hosts.

## Platform spans

Beside the step traces, each daemon can trace its own work: the hub's requests, store queries, outbound calls and sweep
passes, and the runner's requests, store queries, hub calls and ticks. These spans tell an operator where a daemon spent
its time. They share no trace with a step, and the [Spans](#spans) table does not list them.

- **Turning them on.** Both switches are needed: `platform = true` in the `[tracing]` block of `blizzard-hub.toml` or
  `blizzard-runner.toml`, and an OTLP endpoint in OpenTelemetry's own variables, the same ones the step traces read.
  Either alone leaves them off, and a protocol other than `http/protobuf` turns them off too. With them off, a daemon
  installs no tracer provider.
- **Sampling.** A trace's root, which is a request that arrives with no `traceparent`, a runner tick or a hub sweep
  pass, is kept with probability `platform_sample_ratio`, default `0.01`, which must lie in 0 to 1. A request that
  arrives sampled is always kept. `OTEL_TRACES_SAMPLER` replaces the ratio when it is set.
- **What each daemon traces.** The hub traces each request under its route template, such as
  `GET /api/chunks/{chunk_id}`, each store query, each call to the OAuth provider, the forge and the work sources, and
  each sweep pass as a root named `sweep <name>`. The runner traces the same for its own app, whether served over TCP or
  the unix socket, its calls to the hub, and each tick as a root named `tick` with a child per step. A runner's calls to
  its hub carry `traceparent`.
- **What is left out.** The runner's worker `POST /api/heartbeat` and any `/v1/traces` path make no span, and neither do
  the store queries they run.
- **Names.** `service.name` follows the rule in [Resource attributes](#resource-attributes). Sweep and tick spans carry
  the scope `blizzard.hub.platform` or `blizzard.runner.platform`, each at version `1`; request, query and outbound-call
  spans carry the scope of the library that opened them. Request spans follow the stable HTTP semantic conventions,
  version 1.21.0, and query spans the stable database conventions, version 1.25.0, whatever
  `OTEL_SEMCONV_STABILITY_OPT_IN` says.
- **What never leaves.** The [What never leaves](#what-never-leaves) rules hold here too. A request's query string and a
  query's bound values are never recorded, and nor are headers or bodies.

### Platform attributes

| Attribute                   | Type     | Meaning                                                                                                      |
| --------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `blizzard.caller`           | `string` | Who the verified credential names: `runner`, `board`, `operator` or `worker`; absent when none was verified. |
| `blizzard.chunk.id`         | `string` | The chunk a request's route names.                                                                           |
| `blizzard.cli.command`      | `string` | The CLI command a worker ran, as a span of the scope `blizzard.cli`.                                         |
| `blizzard.lease.id`         | `string` | The lease a worker's span arrived under, stamped by the runner.                                              |
| `blizzard.runner.id`        | `string` | The runner the span belongs to, or the runner a request authenticated as.                                    |
| `blizzard.tick.step`        | `string` | The tick step a child span covers.                                                                           |
| `error.type`                | `string` | The error class when the CLI's request failed.                                                               |
| `http.request.method`       | `string` | The HTTP method of the CLI's request to a daemon.                                                            |
| `http.response.status_code` | `int`    | The status the daemon answered the CLI's request with.                                                       |
| `process.exit.code`         | `int`    | The exit code of the CLI command.                                                                            |
| `server.address`            | `string` | The host the CLI's request went to.                                                                          |
| `server.port`               | `int`    | The port the CLI's request went to.                                                                          |
| `url.full`                  | `string` | The CLI request's URL, without query string or fragment.                                                     |

## Checking on tracing

`blizzard hub traces status` reads `GET /api/traces/status`, open to anyone who can view the fleet. It reports:

- **State.** Tracing is on, off, or rejected. A rejected setting is one the hub cannot honor, such as a protocol other
  than `http/protobuf`; status names the setting and its value, and the fix is to point the hub at an OpenTelemetry
  Collector that receives OTLP over HTTP and fan out from there.
- **Endpoint.** The scheme, host and port only. Any userinfo, path, query or fragment in the configured endpoint is
  dropped when the hub starts, and the export headers are never read, so no credential can appear. An endpoint that does
  not parse as a URL shows as a placeholder.
- **Cursor and lag.** The cursor is how far the sweep has told. Lag is the age of the oldest closed step the cursor has
  not passed, and is empty when nothing waits; a lag under `settle_seconds` is normal, since a step is held that long
  before it is told. An idle fleet shows no lag however old the cursor is.
- **Last export.** When the sweep last told spans, and how many.
- **Last error.** When the newest failure began, and whether it is ongoing. The sweep records only the first failure
  after a success, so during an outage this is when the outage began; it is not ongoing once an export succeeds. Status
  carries no exporter error text, which can hold the endpoint; the details are in the hub's log.

Last export and last error are read from what the sweep recorded, so they survive a restart.

## Telling a window again

`blizzard hub traces replay --since <t> --until <t>` tells every step that closed in the window again. It is how a
window the sweep skipped, or a backend that lost spans, is filled in. A `trace-window-skipped` event's `since` and
`until` paste straight in.

- **Same ids as the live sweep.** A replay assembles spans the way the sweep does, so a step's trace and span ids are
  the ones it was told under before. A backend that dedupes on those ids sees no duplicates.
- **The live cursor does not move.** A replay records no event and leaves the sweep's cursor, failure state and backoff
  as they were, so it can run beside a live sweep.
- **The window is bounded.** It is half-open, from `since` up to but not including `until`. It must be positive, and no
  wider than `replay_max_window` seconds in the `[tracing]` block; a wider window is refused with the limit named.
  Replay runs inside the request, so a long window takes a while, and a command line client waits up to ten minutes.
- **`--dry-run` sends nothing.** It reports the steps, spans and batches the window would tell, and works with tracing
  off, which is a cheap way to size a window. Without `--dry-run`, a hub with tracing off refuses the replay.

If the exporter refuses a batch, the replay stops and reports what it had told by then.
