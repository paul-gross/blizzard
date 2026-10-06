# Tracing

Every chunk is told as two traces. The **work trace** holds what the chunk did: a `chunk work` span on top, a root span
under it for each step the chunk takes, child spans for the waits inside or around each step, and a link from each step
to the one before. The **lifetime trace** holds the chunk's life at a glance: a `chunk` span from ingest to its finish,
one span for each step, and the waits no step owns. A runner tells the lease it ran into the work trace, under the step
root (see [Runner spans](#runner-spans)). This page describes the shape of both traces, so a backend can be built
against them and an operator can read one. The shape is a versioned contract —
[`contracts/traces/`](../../contracts/traces/README.md) pins it, and
[`docs/versioning.md`](../versioning.md#the-trace-contract) owns what may change.

## The shape of a trace

A chunk usually rests `not_ready` far longer than it works. Told as one trace, the backlog would stretch the time axis
and shrink the steps, where the detail lives, to a sliver at its end. So the chunk's life and its work are separate
traces, each at its own time scale.

The hub tells both traces once, when the chunk finishes, and never again. A chunk still in flight has no lifetime trace,
and its work trace shows its steps under a work root that has not arrived.

### The work trace

A **step** is one attempt at a node by a runner or the hub, or one human decision at a gate. Every step of a chunk is in
the chunk's work trace; an open step is not told until it closes. The root span is `step <node>` for an attempt and
`gate <node>` for a decision. It carries the step's dimensions, its token and cost totals and its wait totals, and its
start and end are the step's own. Its parent is the **work root**.

The work root is named `chunk work` and covers the chunk from its first step's start to its first terminal fact, `done`
or `stopped`. Every step root, gate roots included, is its direct child. It carries the outcome
(`blizzard.chunk.outcome`) and the chunk's totals: steps, bounces, tokens and cost. It links to the root of the lifetime
trace, with the reason `lifetime`. A chunk that stopped before any step closed has no work trace. The work trace holds
none of the chunk's own waits (backlog, escalation or pause) and no completion marker; those are in the lifetime trace.
The waits that belong to a step stay under its root.

### The lifetime trace

The lifetime trace's root is named `chunk` and covers the chunk from ingest to its first terminal fact. It carries the
outcome, the time the chunk rested in the backlog (`blizzard.chunk.backlog_ms`), the time from when it left the backlog
to its finish (`blizzard.chunk.active_ms`) and the chunk's totals. It is one level deep: under the root hang

- **a span for each step and gate,** named for its node, such as `triage` or `build`. It covers the step's whole share
  of the chunk's life: from the start of its `queue wait` and `claim` (else the step's start), but never before the end
  of the previous step's span or of a backlog or escalation wait that ended by the step's start, and never after the
  step's own start, to the later of the step's end and its close, which takes in a gate's `decision pickup`. Step spans
  never overlap: a span that would run past the next step's start ends there. It carries the step's dimensions,
  including its node, outcome and epoch, its token and cost totals, its wait totals (`blizzard.step.wait.*`) and
  `blizzard.step.kind`, which is `step` or `gate`. It links to that step's root in the work trace, with the reason
  `work`. A step that ends by escalating has an `ERROR` status.
- **the waits that belong to the chunk, not a step:** `backlog wait` while the chunk rests `not_ready`, from ingest to
  the earliest of its first promotion, its first lease and its finish; `escalation wait` while it sits `needs_human`,
  from the escalation to the first requeue, restart (including a migration restart) or lease mint after it, or to the
  finish if nothing did; and `pause wait` while it is paused and nothing else covers it, from the pause to the resume.
  Every wait is clipped around the step spans, and a pause also around the other waits, so what is left of a pause that
  began inside one starts once that step span ended or that wait ended, and a pause can split into several `pause wait`
  spans. No two waits overlap, and none overlaps a step span. A chunk is promoted once, so it has one `backlog wait`.
- **a marker for a later completion.** A `stopped` chunk can still be hand-completed, and the later fact decides its
  status. The root keeps its `stopped` outcome and end; a zero-length `chunk completed` span, at the completion instant
  and carrying the outcome `done`, records the completion.

Every lifetime span leaves under the `service.name` `blizzard-chunk`, so a backend keeps the lifetime spans apart from
the work spans and nothing is counted twice. A backend that maps a service name to a dataset gets a dataset of its own
for them.

The only empty space left in a chunk's lifetime trace is time nothing accounts for.

### Inside a step

Children hang off a step's root:

- the waits before a step — `queue wait`, then `claim` — start before the step does, so the root is not stretched to
  cover them. A chunk that waited a day for a runner reads as a day-long child beside an hour-long step.
- `ask`, `pause` and `hub exec` sit inside the step.
- a gate's only child is `decision pickup`, which starts as the gate ends.

Events on the root record each harness invocation (`invocation`), each time the hub polled a pending hub step
(`hub poll pending`) and a bounce (`bounce`). An event's time never falls after the span's end.

A step's root also links to the previous step's root, with a `blizzard.link.reason` saying why this step follows that
one; the nesting does not carry the reason:

- `next` — The chunk moved on to the next node.
- `retry` — The previous step ended without moving the chunk, and this one is at the same node.
- `bounce` — The previous step ended in a bounce.
- `restart` — An operator restarted the chunk between the two steps.
- `migration` — The chunk moved to another graph between the two steps.

Two more reasons link across the traces of one chunk: `work`, from a lifetime step span to the step root it summarizes,
and `lifetime`, from the work root to the lifetime root.

A step root that ends by escalating has an `ERROR` status, as has its span in the lifetime trace; every other span's
status is unset.

## Spans

| Span                        | Role                    | What it covers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `step <node>`               | `step`                  | One runner or hub attempt at a node, the root of a step, under the work root.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `gate <node>`               | `gate`                  | One human decision at a node, the root of a step, under the work root.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `queue wait`                | `queue`                 | The chunk waiting claimable, pauses excluded.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `claim`                     | `claim`                 | From the claim to the runner starting the step.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `ask`                       | `ask`                   | A question to a person, from asked to answered.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `pause`                     | `pause`                 | The fleet paused during the step.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `decision pickup`           | `pickup`                | From a person deciding a gate to the decision being picked up.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `hub exec`                  | `hub-exec`              | A hub node's run holding its execution slot.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `chunk work`                | `chunk`                 | The chunk's work, from its first step's start to its first terminal fact, `done` or `stopped`, the root of its work trace. Told once, when the chunk finishes.                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `chunk`                     | `chunk/lifetime`        | The chunk from ingest to its first terminal fact, `done` or `stopped`, the root of its lifetime trace. Told once, when it finishes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `<node>`                    | `chunk/step`            | One step or gate of the chunk, named for its node, under the lifetime root; links to the step's root in the work trace. It runs from the start of the step's queue wait and claim, else its start, but never before the end of the previous step span or of a backlog or escalation wait that ended by its start, and never after its own start, to the later of its end and its close, so a gate's decision pickup is inside it; step spans never overlap, and it carries the step's wait totals. The work root keeps the role `chunk` for its unchanged span id; the lifetime spans are `chunk/*`. |
| `chunk completed`           | `chunk/completed`       | A stopped chunk hand-completed, a zero-length span at the completion under the lifetime root.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `backlog wait`              | `chunk/backlog-wait`    | The chunk resting `not_ready`, from ingest to its first promotion, its first lease or its finish, less what a step span covers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `escalation wait`           | `chunk/escalation-wait` | The chunk parked `needs_human`, from the escalation to the requeue, restart, migration restart or lease mint that released it, less what a step span covers.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `pause wait`                | `chunk/pause-wait`      | The chunk paused while no step span and no other chunk wait covered it, from the pause (or the end of the step span it began in) to the resume.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `worker <node>`             | `runner/worker`         | One runner lease, from its minting to its close, parented to the hub's step root.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `invoke_agent <session>`    | `runner/invocation`     | One harness invocation in the lease; plain `invoke_agent` when the node declares no session.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `parked on ask`             | `runner/ask-park`       | The worker parked on a question until it resumed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `parked on pause`           | `runner/pause-park`     | The worker parked by a fleet pause until it resumed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `provider overload backoff` | `runner/overload`       | The worker backing off after the provider reported overload.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `takeover`                  | `runner/takeover`       | A person took the worker's session over, until they handed it back.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

## Attributes

Every child span carries the root's dimensions, so a backend can filter a wait by chunk, node or runner without joining
back to the root. Measures — tokens, cost and the wait totals — ride the root only among the work trace's spans; a
lifetime step span repeats the token and cost totals and the wait totals. The invocation attributes ride the
`invocation` event on the root and the runner's `invoke_agent` spans, and the link reason rides the link. Each attribute
names the roles it rides in [`dictionary.json`](../../contracts/traces/dictionary.json). Attributes marked optional are
absent rather than empty when there is nothing to say.

| Attribute                                  | Type       | Meaning                                                                                                                                                                                                       |
| ------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blizzard.chunk.id`                        | `string`   | The chunk the span belongs to.                                                                                                                                                                                |
| `blizzard.chunk.work_refs`                 | `string[]` | The chunk's work items as source-native tokens, such as `acme#42`.                                                                                                                                            |
| `blizzard.graph.name`                      | `string`   | The name of the graph the chunk was on.                                                                                                                                                                       |
| `blizzard.graph.id`                        | `string`   | The id of that graph version.                                                                                                                                                                                 |
| `blizzard.node.name`                       | `string`   | The node the step was at.                                                                                                                                                                                     |
| `blizzard.node.id`                         | `string`   | The id of that node in its graph version.                                                                                                                                                                     |
| `blizzard.node.executor`                   | `string`   | Who ran the step: `runner`, `hub` or `human`.                                                                                                                                                                 |
| `blizzard.step.epoch`                      | `int`      | The attempt counter the step ran under.                                                                                                                                                                       |
| `blizzard.step.visit`                      | `int`      | How many times the chunk has arrived at this node, counting this one.                                                                                                                                         |
| `blizzard.step.outcome`                    | `string`   | How the step ended: `transitioned`, `gated`, `migrated`, `escalated`, `released`, `stopped`, `completed`, `superseded`, `decided` or `restarted`.                                                             |
| `blizzard.step.choice`                     | `string`   | The name of the choice that moved the chunk on.                                                                                                                                                               |
| `blizzard.step.to_node.name`               | `string`   | The node the step moved the chunk to; `graph:<name>` for a move to another graph.                                                                                                                             |
| `blizzard.step.preceded_by`                | `string`   | What sits between this step and the previous one: `restart`, `requeue` or `released-claim`.                                                                                                                   |
| `blizzard.step.kind`                       | `string`   | Whether the step was a `step` or a `gate`.                                                                                                                                                                    |
| `blizzard.runner.id`                       | `string`   | The runner that held the step or ran the lease.                                                                                                                                                               |
| `blizzard.runner.name`                     | `string`   | The latest registered name of the runner that held the step or ran the lease, as of when the span was told.                                                                                                   |
| `blizzard.harness.id`                      | `string`   | The harness: on a step, the one its last invocation that recorded one ran under; on a runner span, the one the lease or invocation ran under.                                                                 |
| `blizzard.harness.version`                 | `string`   | The version of that harness.                                                                                                                                                                                  |
| `blizzard.step.models`                     | `string[]` | The distinct models the step's invocations used, in first-use order.                                                                                                                                          |
| `blizzard.bounce.cause`                    | `string`   | Why the chunk was bounced back from this step.                                                                                                                                                                |
| `blizzard.ask.answered`                    | `bool`     | Whether a person had answered the question when the span ended.                                                                                                                                               |
| `blizzard.clock_skew`                      | `bool`     | Set when the answer is stamped before the question, so the span is clamped to zero length.                                                                                                                    |
| `blizzard.link.reason`                     | `string`   | Why a span links to the one it names: `next`, `retry`, `bounce`, `restart` or `migration` for the step before; `work` for the step root a lifetime span summarizes; `lifetime` for the lifetime trace's root. |
| `blizzard.step.input_tokens`               | `int`      | Input tokens the step's invocations used, not counting cache.                                                                                                                                                 |
| `blizzard.step.output_tokens`              | `int`      | Output tokens the step's invocations produced.                                                                                                                                                                |
| `blizzard.step.cache_read_tokens`          | `int`      | Input tokens the step's invocations read from the cache.                                                                                                                                                      |
| `blizzard.step.cache_create_tokens`        | `int`      | Input tokens the step's invocations wrote to the cache.                                                                                                                                                       |
| `blizzard.step.cost.usd`                   | `double`   | The step's cost in US dollars, including any estimate.                                                                                                                                                        |
| `blizzard.step.cost.estimated`             | `bool`     | Whether part of the cost is a subscription estimate rather than a billed amount.                                                                                                                              |
| `blizzard.step.cost.partial`               | `bool`     | Whether some invocation reported no cost, so the total is a floor.                                                                                                                                            |
| `blizzard.step.wait.queue_ms`              | `int`      | Milliseconds the chunk waited claimable before a runner or the hub took it.                                                                                                                                   |
| `blizzard.step.wait.claim_ms`              | `int`      | Milliseconds between the claim and the step starting.                                                                                                                                                         |
| `blizzard.step.wait.ask_ms`                | `int`      | Milliseconds the step spent waiting on a person's answer.                                                                                                                                                     |
| `blizzard.step.wait.pause_ms`              | `int`      | Milliseconds the step spent paused.                                                                                                                                                                           |
| `blizzard.step.wait.pickup_ms`             | `int`      | Milliseconds between a person deciding a gate and the decision being picked up.                                                                                                                               |
| `blizzard.chunk.outcome`                   | `string`   | How the chunk finished: `done` or `stopped`. A `chunk completed` marker reads `done`.                                                                                                                         |
| `blizzard.chunk.backlog_ms`                | `int`      | Milliseconds the chunk rested `not_ready` before it left the backlog.                                                                                                                                         |
| `blizzard.chunk.active_ms`                 | `int`      | Milliseconds from when the chunk left the backlog to its finish.                                                                                                                                              |
| `blizzard.chunk.steps`                     | `int`      | How many steps the chunk took, gates included.                                                                                                                                                                |
| `blizzard.chunk.bounces`                   | `int`      | How many times the chunk was bounced back.                                                                                                                                                                    |
| `blizzard.chunk.input_tokens`              | `int`      | Input tokens the chunk's invocations used, not counting cache.                                                                                                                                                |
| `blizzard.chunk.output_tokens`             | `int`      | Output tokens the chunk's invocations produced.                                                                                                                                                               |
| `blizzard.chunk.cache_read_tokens`         | `int`      | Input tokens the chunk's invocations read from the cache.                                                                                                                                                     |
| `blizzard.chunk.cache_create_tokens`       | `int`      | Input tokens the chunk's invocations wrote to the cache.                                                                                                                                                      |
| `blizzard.chunk.cost.usd`                  | `double`   | The chunk's cost in US dollars, including any estimate.                                                                                                                                                       |
| `blizzard.chunk.cost.estimated`            | `bool`     | Whether part of the cost is a subscription estimate rather than a billed amount.                                                                                                                              |
| `blizzard.chunk.cost.partial`              | `bool`     | Whether some invocation reported no cost, so the total is a floor.                                                                                                                                            |
| `blizzard.invocation.kind`                 | `string`   | What the invocation was: `spawn`, `resume` or `judge`; a nudge reads `resume`.                                                                                                                                |
| `blizzard.invocation.input_tokens`         | `int`      | Input tokens, not counting cache.                                                                                                                                                                             |
| `blizzard.invocation.output_tokens`        | `int`      | Output tokens.                                                                                                                                                                                                |
| `blizzard.invocation.cache_read_tokens`    | `int`      | Input tokens read from the cache.                                                                                                                                                                             |
| `blizzard.invocation.cache_create_tokens`  | `int`      | Input tokens written to the cache.                                                                                                                                                                            |
| `blizzard.invocation.cost.usd`             | `double`   | The invocation's cost in US dollars, including any estimate.                                                                                                                                                  |
| `blizzard.invocation.cost.estimated`       | `bool`     | Whether the cost is a subscription estimate.                                                                                                                                                                  |
| `gen_ai.response.model`                    | `string`   | The model that answered.                                                                                                                                                                                      |
| `gen_ai.usage.input_tokens`                | `int`      | All input tokens, cache reads and writes included.                                                                                                                                                            |
| `gen_ai.usage.output_tokens`               | `int`      | Output tokens.                                                                                                                                                                                                |
| `gen_ai.usage.cache_read.input_tokens`     | `int`      | Input tokens read from the cache.                                                                                                                                                                             |
| `gen_ai.usage.cache_creation.input_tokens` | `int`      | Input tokens written to the cache.                                                                                                                                                                            |
| `blizzard.lease.id`                        | `string`   | The runner lease the span belongs to.                                                                                                                                                                         |
| `blizzard.lease.close_reason`              | `string`   | Why the lease closed, such as `transitioned`; either escalation reads `escalated`.                                                                                                                            |
| `blizzard.session.name`                    | `string`   | The declared session the node ran in.                                                                                                                                                                         |
| `blizzard.model.resolved`                  | `string`   | The model the runner resolved the session's tier to.                                                                                                                                                          |
| `blizzard.effort.resolved`                 | `string`   | The effort the runner resolved the session's effort to.                                                                                                                                                       |
| `blizzard.invocation.nudge`                | `bool`     | Whether a nudge opened the invocation.                                                                                                                                                                        |
| `blizzard.invocation.generation`           | `int`      | The worker generation the invocation belongs to.                                                                                                                                                              |
| `blizzard.invocation.end_source`           | `string`   | What ended the invocation: `session_end`, `next_invocation` or `lease_close`.                                                                                                                                 |
| `blizzard.overload.streak`                 | `int`      | The backoff's place in its streak of overloads, counting from 1.                                                                                                                                              |
| `blizzard.context.tokens`                  | `int`      | The session's context size in tokens when sampled.                                                                                                                                                            |
| `blizzard.check.index`                     | `int`      | The check's position in the node's declared checks, counting from 1.                                                                                                                                          |
| `blizzard.check.passed`                    | `bool`     | Whether that check passed.                                                                                                                                                                                    |
| `blizzard.checks.passed`                   | `bool`     | Whether every check passed.                                                                                                                                                                                   |
| `blizzard.checks.count`                    | `int`      | How many checks ran.                                                                                                                                                                                          |
| `gen_ai.operation.name`                    | `string`   | Always `invoke_agent`.                                                                                                                                                                                        |
| `gen_ai.agent.name`                        | `string`   | The declared session the node ran in.                                                                                                                                                                         |
| `gen_ai.conversation.id`                   | `string`   | The harness session id, for a worker invocation.                                                                                                                                                              |
| `gen_ai.request.model`                     | `string`   | The model the runner resolved for the session.                                                                                                                                                                |

Cost is in US dollars. When a subscription estimate stands in for a billed amount, `blizzard.step.cost.estimated` says
so, and `blizzard.step.cost.partial` says some invocation reported no cost at all. `gen_ai.*` names follow the
OpenTelemetry GenAI semantic conventions at version `1.44.0`; their input-token total counts cache reads and writes,
where the `blizzard.invocation.*` counts do not.

## Resource attributes

The hub's spans are emitted under the instrumentation scope `blizzard.hub.fleet_spans` and a runner's under
`blizzard.runner.runner_spans`, both at version `3`. The hub's lifetime spans are emitted under the same scope and
version, with their own `service.name`. The resource carries:

| Attribute                       | Type     | Meaning                                                                                                                                                                                                                   |
| ------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `service.name`                  | `string` | The service name; `blizzard-hub` or `blizzard-runner` (`blizzard-cli` for an operator command) unless `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` names one. The hub's lifetime spans always read `blizzard-chunk`. |
| `service.version`               | `string` | The running hub's or runner's version.                                                                                                                                                                                    |
| `blizzard.trace.schema_version` | `string` | The version of this contract.                                                                                                                                                                                             |

`blizzard.trace.schema_version` is `3`. A breaking change to the shape raises it together with the scope version; a
backend that keys on either can tell shapes apart.

## Trace and span ids

Ids are derived, never random, so the same chunk always lands in the same traces and a backend can find a chunk's traces
without a lookup. A step's key is `<chunk id>/<epoch>` for an attempt and `<chunk id>/<epoch>/gate/<decision id>` for a
gate.

- The trace id is the first 16 bytes of the SHA-256 of `blizzard-trace/v2/` followed by the chunk id. Every span of the
  work trace takes it: the work root, and the step, gate, runner, platform and worker spans.
- A span id is the first 8 bytes of the SHA-256 of `blizzard-span/v1/`, the step's key, `/`, the span's role and `/`,
  and a discriminator. Keyed on the step, no two spans of a chunk collide. The discriminator is the source row's own id
  for a role that can occur more than once in a step (`ask`, `pause`, `hub-exec`) and empty otherwise.
- A runner span's role is prefixed `runner/`, and its discriminator is the lease id for `runner/worker`,
  `<generation>/<kind>` for `runner/invocation`, and the source row's id for the other runner roles.
- The work root's span id is the first 8 bytes of the SHA-256 of `blizzard-chunk-span/v2/`, the chunk id and `/chunk/`.
- The lifetime trace id is the first 16 bytes of the SHA-256 of `blizzard-lifetime-trace/v1/` followed by the chunk id,
  so it differs from the work trace's.
- A lifetime span id is the first 8 bytes of the SHA-256 of `blizzard-lifetime-span/v1/`, the chunk id, `/`, the span's
  role (`chunk/lifetime`, `chunk/step`, `chunk/completed`, `chunk/backlog-wait`, `chunk/escalation-wait` or
  `chunk/pause-wait`), `/`, and a discriminator. The discriminator is empty for the root, the step's key for a step
  span, and the instant the span begins as UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` for a wait or the marker.
- An all-zero digest has its last byte set to `01`, so an id is never zero.
- Every span is sampled.

For chunk `ch_1`, the work trace id is `1b74f66284990c6da69e0005db74f243` and the work root's id is `3df6797481a5fac6`;
the root of its attempt `ch_1/1` is `88d23b1ca9cc34d2`. Its lifetime trace id is `f09d101be501667a643be80a461de251` and
the lifetime root's id is `2217fceaa579bb76`. The full set of worked vectors is in
[`dictionary.json`](../../contracts/traces/dictionary.json).

## What never leaves

No span carries prompt text, transcript content or check output; an ask's question or answer, or a gate's choice
descriptions; a bounce's envelope; the name or login of anyone who resolved a gate or answered an ask; or a chunk's
title or body. Work items, names, ids, counts, durations and costs are the whole of what a trace holds.

One opt-in exception: with [harness telemetry](#harness-telemetry) on, Claude Code's own identity attributes
(`user.email`, `user.account_uuid`, `user.id`, `organization.id`) are exported with its telemetry. They are the
operator's choice to send, and no other rule here is lifted.

## Runner steps start at the claim

A runner step starts when the hub records the lease, not when the worker spawned. That gap is usually one runner drain,
but a runner that cannot reach the hub stores what it has and forwards it later, and then the gap can run to hours. The
step's root keeps the hub's start, so a runner step's start is biased early by that gap, and its `claim` span ends at
the same instant.

## Fanning one stream to two backends

[`packaging/otel-collector/collector.yaml`](../../packaging/otel-collector/collector.yaml) is an example OpenTelemetry
Collector configuration that receives OTLP over HTTP and sends every trace to two backends. It is validated against
`otelcol-contrib` 0.162.0 by `mise run collector-config-check`. It also carries `metrics` and `logs` pipelines to the
hosted exporter, for the signals a runner with [harness telemetry](#harness-telemetry) sends; that exporter
(`HOSTED_TRACES_*` variables) carries all three signals despite its variable names.

- **Receiver.** One `otlp` receiver on the HTTP protocol, port 4318 — protobuf over HTTP is the only protocol the hub
  will send.
- **Processor.** `batch` groups spans before export, so the backends see fewer, larger requests.
- **Exporters.** `otlp/store` sends over gRPC to a self-hosted store such as Tempo; `otlphttp/hosted` sends over HTTP
  with an API key header to a hosted service such as Honeycomb. Both feed from the one `traces` pipeline; the `metrics`
  and `logs` pipelines feed `otlphttp/hosted` only.
- **Credentials.** Endpoints and the API key are read from the environment (`TRACE_STORE_ENDPOINT`,
  `TRACE_STORE_INSECURE`, `HOSTED_TRACES_ENDPOINT`, `HOSTED_TRACES_API_KEY`), never written into the file.

Adjust the endpoints and the header name to your backends; keep the pipeline shape.

## Runner spans

A runner tells each lease it closes into the chunk's work trace, under the instrumentation scope
`blizzard.runner.runner_spans` at version `3`. A `worker <node>` span covers the lease, with the lease's invocations,
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
- **Which runner.** Each span carries the id and name of the runner's latest registration with its hub, read from its
  store as the lease is told, so a lease told after a rename by restart carries the new name, whenever it was minted.
  Until its first registration the runner tells nothing: the sweep holds its cursor, so the leases that close meanwhile
  are told once it registers.
- **Events.** The runner reports `trace-export-failed`, `trace-export-recovered`, `trace-window-skipped` and
  `trace-config-rejected` the same way the hub does. They reach the hub's event log attributed to the runner, carrying
  no chunk or lease. A rejected setting is reported once each time the runner starts, and the runner keeps working with
  tracing off.

**Meeting the hub's step.** No message passes between the daemons. Both derive the trace id from the chunk id, and the
`worker` span's parent is the span id the hub derives for that step's root, so a backend shows the runner's spans under
the hub's step. A runner span id uses the same derivation with its role prefixed `runner/`. If the hub has tracing off,
the runner's spans still group by trace id under a parent that never arrives.

**The gap between them.** The two daemons stamp times with their own clocks, and a runner mints its lease before the hub
hears of it, so a `worker` span can start before its parent. Neither side clamps to the other. The gap is the start bias
described under [Runner steps start at the claim](#runner-steps-start-at-the-claim), made visible; a gap that does not
match the runner's drain cadence points at clock skew between the hosts.

## Platform spans

Beside the chunk traces, each daemon can trace its own work: the hub's requests, store queries, outbound calls and sweep
passes, and the runner's requests, store queries, hub calls and ticks. These spans tell an operator where a daemon spent
its time. Most of them form their own traces; the ones made on a step's behalf join the chunk's work trace under the
step's root, as described under **Nesting under a step** below. The [Spans](#spans) table does not list them.

- **Turning them on.** Both switches are needed: `platform = true` in the `[tracing]` block of `blizzard-hub.toml` or
  `blizzard-runner.toml`, and an OTLP endpoint in OpenTelemetry's own variables, the same ones the chunk traces read.
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
- **Nesting under a step.** A platform span made on one step's behalf parents on that step's derived root or `hub exec`
  span, using the ids in [Trace and span ids](#trace-and-span-ids). Such a span is always kept, whatever the sample
  ratio, because its parent counts as sampled.
  - *The runner's own calls.* The runner's completion and judgement-decision submissions, its gate apply, its envelope
    re-reads and its hub-node polls each send their request under the step they serve. A completion or decision nests
    under its attempt's root, and a gate apply under the gate's root. An envelope re-read nests under the newest attempt
    the runner knows of; with no known epoch it starts a trace of its own. A hub-node poll nests under the hub step it
    drives, the step after the newest epoch the chunk's status reports. The runner's fact drain belongs to no step.
  - *The hub's `run:` steps.* Each `run:` step a hub node executes is a span named `hub run step`, a direct child of
    that step's `hub exec` span. A step its `produces:` skips makes no span, and no span wraps the node as a whole. The
    span carries the step's exit code and its authored name, never its command, output or environment. The request that
    drove the node, which is a hub-advance poll or a completion or migration apply, carries a link to the `hub exec`
    span.
  - *A restart between poll and exit.* A poll's spans parent on a step root the trace sweep exports only once that hub
    step closes. A restart between a poll and the exit of the hub node it drove can leave that root never exported. A
    trace backend then shows the poll's spans under a missing parent.
- **Continuing an incoming trace.** The hub continues an incoming `traceparent` only for a caller it authenticates: a
  runner bearer that resolves to a registered, unrevoked runner, or a human session or operator bearer under the
  configured auth mode. For any other request, including an unknown or absent bearer and every human request under
  `auth.mode = none`, the hub drops `traceparent`, `tracestate` and `baggage` and the request starts a new root. The
  runner continues an incoming `traceparent` as before.
- **Which runner.** The runner stamps each of its platform spans with the id and name of its latest registration, read
  as the span starts, so a rename by restart shows from the first span after it; a span started before the runner's
  first registration is never exported. The hub stamps a request a runner bearer authenticated with that runner's id
  and the name the hub holds for it.
- **What is left out.** The runner's worker `POST /api/heartbeat` and any `/v1/traces` path make no span, and neither do
  the store queries they run.
- **Names.** `service.name` follows the rule in [Resource attributes](#resource-attributes). Sweep and tick spans carry
  the scope `blizzard.hub.platform` or `blizzard.runner.platform`, each at version `1`; request, query and outbound-call
  spans carry the scope of the library that opened them. Request spans follow the stable HTTP semantic conventions,
  version 1.21.0, and query spans the stable database conventions, version 1.25.0, whatever
  `OTEL_SEMCONV_STABILITY_OPT_IN` says.
- **What never leaves.** The [What never leaves](#what-never-leaves) rules hold here too. A request's query string and a
  query's bound values are never recorded, and nor are headers or bodies.

### Arrival order

Spans reach the collector in the order they are made, not the order of the trace they belong to.

- **Platform spans arrive live.** A request, query or outbound call leaves with its daemon's next batch, while the step
  is still running.
- **The hub's step root arrives late.** The hub tells a step only once it has closed and `settle_seconds` have passed,
  so the root and its waits arrive at least that long after the step ends, on the next sweep (`sweep_seconds`) after
  that.
- **The work root and the lifetime trace arrive last.** The hub tells them, in one batch, only when the chunk finishes,
  so every step of a chunk is in the backend long before the work root that parents them, and a chunk still in flight
  has neither.
- **The runner's spans arrive after its own sweep.** A `worker` span and its children leave when the runner's sweep
  tells the lease, `settle_seconds` after it closes, so they arrive after the lease's platform spans.

A trace backend assembles a trace when it is queried, so arrival order changes nothing there. A stage that decides about
a trace while spans are still arriving does see it, as the next two paragraphs describe.

**Tail sampling.** A collector stage that waits a bounded time before it decides splits a trace or drops it: whatever
arrives after the decision is handled as the decision was, or on its own. A chunk's work trace grows over the chunk's
whole work, from its first span to a work root that arrives after the last step, which can be days later. No
`decision_wait` a collector can reasonably hold covers that, so a tail-sampling stage cannot hold a chunk's work trace
whole. The lifetime trace arrives all at once, at the finish. Run no tail sampling ahead of this pipeline.

The `tail_sampling` processor counts its `decision_wait` from the first span of a trace, which is its first platform
span, and a policy that keeps a trace only if it holds the work root or a step's root decides long before either
arrives. The `tail_sampling` processor of `otelcol-contrib` 0.162.0 then drops the trace, including the spans that
arrive afterwards. A collector that held every span of a trace until the work root arrived would need memory for every
chunk in flight, which has no useful bound.

**Platform spans without fleet spans.** The `platform` switch and the chunk traces' own export are independent. With
platform spans on and the chunk traces off, the spans made on a step's behalf still carry the chunk's trace id and a
parent that is never exported, so a backend shows them grouped under a root that is missing. Turn both on.

### Worker spans

A worker's own tools can send spans to the runner that spawned them. The runner serves OTLP over HTTP at
`POST /v1/traces` on the same TCP port and unix socket as its API, and forwards what it accepts through its own platform
pipeline, so the spans leave to the same endpoint, through the same redacting export, as its own. The receiver exists
only while platform tracing is on and the runner has registered with its hub; otherwise the path answers `404`, and a
sender is expected to carry on.
[Harness telemetry](#harness-telemetry) adds `POST /v1/metrics` and `POST /v1/logs`, which follow the same
authentication, encodings and caps, with the exceptions listed there.

- **Authentication.** The worker's lease token, in `X-Blizzard-Lease-Token` or as an `Authorization: Bearer` header. The
  request names no lease: the runner finds the lease the token was minted for, which must still be active or under an
  open takeover. A missing, unknown or closed-lease token is refused `403`.
- **Encodings.** `application/json` and `application/x-protobuf`, both identity-encoded; any `Content-Encoding` but
  `identity` is refused `415`, as is any other content type. A malformed body is refused `400`. A `200` carries an OTLP
  `ExportTraceServiceResponse` in the request's encoding, whose `partial_success.rejected_spans` counts the spans that
  were refused.
- **What the CLI sends.** Each `blizzard runner` command a worker runs, other than `heartbeat`, is one span named for
  the command, such as `runner chunk history`, a child of the step's span, whose context the runner passes down in
  `BLIZZARD_TRACEPARENT`. Its own context goes out as `traceparent` on every request the command makes. Just before the
  command exits, the span is posted here within 100 ms in total, whatever the outcome. Tracing adds at most 5 ms at p95
  to a command's own duration, which a service-tier test pins. A failed send is silent and never changes the command's
  output or exit code; set `BLIZZARD_TRACE_DEBUG` to see it on stderr. The span records the command's names, never an
  argument or option value.
- **What is kept.** A span is kept only if it belongs to the trace of the lease's own chunk and arrives under the scope
  `blizzard.cli`. It is dropped, and counted, if its span id is the work root's, a step root's or a step's `queue wait`
  or `claim` the hub derives for the lease's chunk at an epoch up to the lease's (its own step's included), or if its
  parent is the work root. The runner cannot derive a gate root, or the `ask`, `pause`, `hub exec` or `decision pickup`
  spans, whose ids come from a decision, question, pause or slot id, so a span carrying one is kept; a backend that
  dedupes on span ids can have those shadowed by a worker holding the lease token. Anything else is dropped, and
  counted. Events, links, trace state and the status message are never kept.
- **What is rewritten.** The span leaves under the runner's resource with `service.name` set to `blizzard-cli`, whatever
  the sender said. Only the CLI attributes in [Platform attributes](#platform-attributes) are kept, and only with the
  value type listed there; the runner then stamps `blizzard.caller` as `worker` and `blizzard.chunk.id` and
  `blizzard.lease.id` from the lease, replacing anything the sender set. A URL attribute loses its query string and
  fragment, as every platform span's does.
- **Caps.** A request body over 1 MiB is refused `413`. Each lease may send 1000 spans in a burst and 50 a second
  sustained; a request whose kept spans exceed what its bucket holds is refused `429` whole, and its spans are counted
  as dropped. Only kept spans are charged, so spans the receiver drops cannot use up the budget. A span keeps at most 64
  of its attributes, and a span name, scope version or string attribute value is cut at 1024 characters.

#### Worker programs

With `worker_programs = true` in the `[tracing]` block of `blizzard-runner.toml`, default `false` and effective only
alongside `platform = true`, the runner also lets the programs a worker runs send their own spans. Each invocation's
environment then carries `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (the runner's `/v1/traces`),
`OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf` and `OTEL_EXPORTER_OTLP_TRACES_HEADERS` (the lease token). It also
sets winter's own switch, which ignores the generic names: `WINTER_OTEL_EXPORTER_OTLP_ENDPOINT` (the runner's base URL;
winter appends `/v1/traces`) and `WINTER_OTEL_EXPORTER_OTLP_HEADERS` (the lease token), so `winter` command spans nest
under the step. With it off, no `WINTER_OTEL_*` variable and none of these `OTEL_EXPORTER_*` variables reaches a worker
(a Claude Code worker may still get its own under [harness telemetry](#harness-telemetry)), and `WINTER_OTEL_*` names in
`env_passthrough` are always withheld.

- **What changes.** The receiver keeps spans under any scope with any attributes, and their `service.name` is
  `blizzard-worker-program`, except Claude Code's own scopes, which are `blizzard-claude-code`. Everything else holds:
  only spans inside the presenting lease's work trace are kept, with the same refusals of the work root's and the step
  roots' ids, the runner stamps caller, chunk and lease, and every cap and the redacting export apply. The CLI's own
  spans are unchanged.
- **Naming a program's spans.** The `[tracing.worker_program_services]` table, empty by default, maps an instrumentation
  scope name to the `service.name` its kept spans leave with, for example `winter_cli = "winter-blizzard"`. A scope not
  listed stays `blizzard-worker-program`, or `blizzard-claude-code` for one of Claude Code's three scopes, and the CLI
  scope `blizzard.cli` stays `blizzard-cli`. The sender's own resource is never read. The runner refuses to start on an
  empty scope or name, a `blizzard.cli` key, a non-string name, or a name that is `blizzard-hub`, `blizzard-runner`,
  `blizzard-chunk` or `blizzard-cli`. The table has no effect unless `worker_programs = true` or
  `harness_telemetry = true`, and none on the hub, which has no receiver. A bad entry is a config-load failure and the
  runner does not start, unlike the OpenTelemetry environment settings, which are reported and leave the runner running
  with tracing off.
- **Risk.** Blizzard cannot control what a third-party program puts in its spans; one may record request bodies or query
  parameters. Turn this on only for programs you trust with that.
- **The harness reads these variables too.** An agent harness that honors `OTEL_EXPORTER_*` exports its traces to the
  runner as well. Claude Code does so only with its own telemetry switched on, and its spans then leave as
  `blizzard-claude-code`.
- **`TRACEPARENT`.** Most SDKs do not read it on their own; a program joins the chunk's work trace, under the step's
  root, only if it is configured to.

#### Harness telemetry

With `harness_telemetry = true` in the `[tracing]` block of `blizzard-runner.toml`, default `false` and effective only
alongside `platform = true`, a Claude Code worker exports its metrics, logs and traces to the runner. Measured on
`claude` 2.1.288 to 2.1.289; the managed-settings behavior below is read from `claude`'s code, not run.

- **What a worker gets.** For each signal you have not configured, each invocation's environment carries that signal's
  exporter (`otlp`), endpoint (the runner's `/v1/traces`, `/v1/metrics` or `/v1/logs`), protocol `http/protobuf` and
  header (the lease token), plus `CLAUDE_CODE_ENABLE_TELEMETRY=1` and `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`. Only a
  worker holding a lease token is pointed at the runner, and a takeover's attended session never is.
- **What the runner keeps.** Claude Code's scopes only: `com.anthropic.claude_code` for metrics, `.events` for logs,
  `.tracing` for traces. Each span, data point and log record is stamped with `blizzard.caller=worker`,
  `blizzard.chunk.id`, `blizzard.lease.id`, `blizzard.runner.id` and `blizzard.runner.name`.
- **Where it goes.** Under `service.name` `blizzard-claude-code` through the runner's own OpenTelemetry settings for
  that signal. Claude Code's own resource name is `claude-code`; `[tracing.worker_program_services]` renames by scope.
- **Judgement turns.** A judgement, a `--resume` turn, was observed exporting metrics and logs but no spans on
  `claude` 2.1.289.
- **Yielding.** A signal is left to you when its own exporter or endpoint, or the generic `OTEL_EXPORTER_OTLP_ENDPOINT`,
  is set in the worker's allowlisted env or in the `env` of the settings document the runner passes as `--settings`: the
  published bundle's composed `settings.json` when there is one, otherwise `worker_settings_path`. A `--settings` `env`
  that sets that signal's `OTEL_EXPORTER_OTLP_<SIGNAL>_HEADERS` or `_PROTOCOL` yields it too, since either would replace
  the runner's own. For traces and logs, `BETA_TRACING_ENDPOINT` together with `ENABLE_BETA_TRACING_DETAILED` counts
  too, and so does a falsy `CLAUDE_CODE_ENABLE_TELEMETRY` in the `--settings` `env`, which switches telemetry off. The
  variables the runner sets itself never cause a yield. With `worker_programs` on, its traces endpoint in the
  process env beats a generic endpoint in settings, so a traces signal reported `operator-configured` can still reach
  the runner.
- **Opting out.** Put that signal's exporter, such as `OTEL_METRICS_EXPORTER=none`, in the `--settings` document's
  `env`. An edit to the default `<runner dir>/worker-settings.json` is erased by `runner init`; its durable homes are a
  bundle or a `worker_settings_path` that points elsewhere. The plan is derived when the runner starts, so a change to
  the worker settings `env` takes effect on a runner restart.
- **Outcomes.** `blizzard runner status` and `blizzard runner traces status` show each signal as `captured`,
  `operator-configured (not captured)`, `no runner destination (not captured)` or `off`, one line when all are off; a
  runner with no enabled Claude Code binding is `off`. A captured signal's counts are Claude Code's alone; the traces
  count is its spans, apart from the `worker spans` line. A signal has no runner destination when the runner's own
  OpenTelemetry variables give it none, such as a traces-only endpoint; the runner then sets nothing for it rather than
  letting Claude Code's localhost default take it.
- **Precedence.** A `--settings` or user-settings `env` entry beats a same-named process variable, a
  process signal-specific endpoint beats a settings generic one, and managed settings win per signal family (read from
  `claude`'s code, not run). The
  record of what was measured and what inferred, and when to measure again, is in
  blizzard-context's
  [`blizzard:manual-claude-code-harness-telemetry`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard/manual.md).
- **Accepted overrides.** A generic endpoint, or an exporter with no endpoint, set only in the user's own Claude
  settings is invisible to the runner, which captures that signal anyway. Where your endpoint wins but you set no
  headers, the lease token goes to it as `X-Blizzard-Lease-Token`. A per-signal `OTEL_EXPORTER_OTLP_<SIGNAL>_HEADERS` or
  `_PROTOCOL` in the user's own Claude settings beats the runner's process variables and replaces its lease-token
  header: the runner refuses that signal `403`, nothing captures it, and status still says `captured`. Your header
  value, an API key say, is then sent to the runner's receiver. Put that signal's exporter in the `--settings`
  document, which opts it out, or set no per-signal headers or protocol.
- **Your destinations switch on.** Claude Code exports nothing until `CLAUDE_CODE_ENABLE_TELEMETRY=1`, which the runner
  sets once any signal is captured, so the destinations you configured start receiving then. Traces also need
  `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, which the runner sets with it; you need both in your settings `env` only
  when the runner captures nothing, such as when every signal is yielded.
- **Receivers.** `POST /v1/metrics` and `POST /v1/logs` take the trace receiver's authentication, encodings and 1 MiB
  cap, but answer `404` unless platform tracing and `harness_telemetry` are both on and the runner has registered. Each
  signal has its own per-lease rate, the same burst and sustained rates as the trace receiver's caps, counted in data
  points or log records,
  charged only for what is kept. A summary
  metric point is refused and counted. Exponential histograms are carried, without their `zero_threshold`. Metrics leave
  through a bounded background queue that drops its oldest batch when full, and logs through the SDK's batch log
  processor. A log record keeps its trace context only inside the lease's work trace.
- **`TRACEPARENT`.** With traces captured Claude Code's spans join the chunk's trace under the step's root. It passes
  its own `claude_code.tool.execution` span on as `TRACEPARENT` to a tool it runs, so a
  [worker program](#worker-programs) that reads `TRACEPARENT` nests under that tool's span. A `blizzard runner`
  command's span still parents on the step's root, since the CLI reads only `BLIZZARD_TRACEPARENT`, which Claude Code
  passes through unchanged.
- **What leaves.** Claude Code's identity attributes (`user.email`, `user.account_uuid`, `user.id`, `organization.id`)
  pass through unchanged; turning this on is the choice to export them. Content logging you enable with `OTEL_LOG_*` is
  exported too, a risk like the one under worker programs.
- **Two destinations.** The runner exports to one. Fan out through your own collector, as under
  [Fanning one stream to two backends](#fanning-one-stream-to-two-backends).

### Operator command spans

A `blizzard hub` or `blizzard runner` command an operator runs can be traced to the OTLP endpoint the operator
configures, with no daemon in between.

- **When it is on.** Only when an OTLP endpoint is set, `OTEL_SDK_DISABLED` is not `true` and `OTEL_TRACES_EXPORTER` is
  not `none`. A worker's own environment never takes this path: a process that carries `BLIZZARD_TRACEPARENT` or
  `BLIZZARD_RUNNER_URL` records only the [worker span](#worker-spans), if any. `host` in either group, a bare
  `blizzard hub` or `blizzard runner`, and `hub record-marker` are never traced.
- **What it records.** Each command is one root span with a trace of its own, always sampled, named for the command,
  such as `hub chunk list` or `runner status`, whichever alias ran it. It carries `blizzard.cli.command` and
  `process.exit.code`, and an error status for a non-zero exit. It never records an argument or option value, and no
  `blizzard.caller`.
- **Where it goes.** `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is used as given; otherwise `OTEL_EXPORTER_OTLP_ENDPOINT` with
  `/v1/traces` appended. The headers come from `OTEL_EXPORTER_OTLP_TRACES_HEADERS`, otherwise
  `OTEL_EXPORTER_OTLP_HEADERS`, as comma-separated `key=value` pairs with percent-decoded values.
- **JSON only.** The span is sent as OTLP/JSON, so `OTEL_EXPORTER_OTLP_PROTOCOL` is not consulted. A backend that
  accepts only protobuf needs a collector in front;
  [`packaging/otel-collector/collector.yaml`](../../packaging/otel-collector/collector.yaml) is one, and a local
  collector is the recommended receiver.
- **The send.** It happens once, as the command ends, and takes at most 500 ms in total, whatever the outcome. A failed
  send is silent and never changes the command's output or exit code; set `BLIZZARD_TRACE_DEBUG` to see it on stderr.
- **Linking to the hub.** The span's `traceparent` goes on the command's requests to the hub and to a local runner,
  never on a call to a third-party provider. The hub continues it only for an authenticated operator, under
  [Continuing an incoming trace](#platform-spans); under `auth.mode = none` the hub's span starts a new root.
- **Names.** `service.name` is `blizzard-cli` unless `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` names one. No
  other resource attribute is sent.

### Platform attributes

| Attribute                         | Type     | Meaning                                                                                                      |
| --------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `blizzard.caller`                 | `string` | Who the verified credential names: `runner`, `board`, `operator` or `worker`; absent when none was verified. |
| `blizzard.chunk.id`               | `string` | The chunk a request's route names.                                                                           |
| `blizzard.cli.command`            | `string` | The CLI command a worker or an operator ran, as a span of the scope `blizzard.cli`.                          |
| `blizzard.hub.run_step.exit_code` | `int`    | The exit code of a hub node's `run:` step.                                                                   |
| `blizzard.hub.run_step.name`      | `string` | A hub node's `run:` step's authored name, never its command line; absent when the step authors none.         |
| `blizzard.lease.id`               | `string` | The lease a worker's span arrived under, stamped by the runner.                                              |
| `blizzard.runner.id`              | `string` | The runner the span belongs to, or the runner a request authenticated as.                                    |
| `blizzard.runner.name`            | `string` | The latest registered name of the runner the span belongs to, or of the runner a request authenticated as.   |
| `blizzard.tick.step`              | `string` | The tick step a child span covers.                                                                           |
| `error.type`                      | `string` | The error class when the CLI's request failed.                                                               |
| `http.request.method`             | `string` | The HTTP method of the CLI's request to a daemon.                                                            |
| `http.response.status_code`       | `int`    | The status the daemon answered the CLI's request with.                                                       |
| `process.exit.code`               | `int`    | The exit code of the CLI command.                                                                            |
| `server.address`                  | `string` | The host the CLI's request went to.                                                                          |
| `server.port`                     | `int`    | The port the CLI's request went to.                                                                          |
| `url.full`                        | `string` | The CLI request's URL, without query string or fragment.                                                     |

## Checking on tracing

`blizzard hub traces status` reads `GET /api/traces/status`, open to anyone who can view the fleet. It reports:

- **State.** Tracing is on, off, or rejected. A rejected setting is one the hub cannot honor, such as a protocol other
  than `http/protobuf`; status names the setting and its value, and the fix is to point the hub at an OpenTelemetry
  Collector that receives OTLP over HTTP and fan out from there.
- **Endpoint.** The scheme, host and port only. Any userinfo, path, query or fragment in the configured endpoint is
  dropped when the hub starts, and the export headers are never read, so no credential can appear. An endpoint that does
  not parse as a URL shows as a placeholder.
- **Cursor and lag.** The cursor is how far the sweep has told. Lag is the age of the oldest closed step or finished
  chunk the cursor has not passed, and is empty when nothing waits; a lag under `settle_seconds` is normal, since a step
  is held that long before it is told. An idle fleet shows no lag however old the cursor is.
- **Last export.** When the sweep last told spans, and how many.
- **Last error.** When the newest failure began, and whether it is ongoing. The sweep records only the first failure
  after a success, so during an outage this is when the outage began; it is not ongoing once an export succeeds. Status
  carries no exporter error text, which can hold the endpoint; the details are in the hub's log.

Last export and last error are read from what the sweep recorded, so they survive a restart.

`blizzard runner traces status` also reports the runner's receivers: the worker spans it accepted and dropped since it
started, and a `harness telemetry` section giving each signal's outcome (see [Harness telemetry](#harness-telemetry))
and, for a captured one, its accepted and dropped counts in spans, data points or log records. These are counts held in
memory, so they reset on a restart; `blizzard hub traces status` has no such line, since the hub has no receiver.

## Telling a window again

`blizzard hub traces replay --since <t> --until <t>` tells every step that closed and every chunk that finished in the
window again. It is how a window the sweep skipped, or a backend that lost spans, is filled in. A `trace-window-skipped`
event's `since` and `until` paste straight in.

- **Same ids as the live sweep.** A replay assembles spans the way the sweep does, so a span's trace and span ids are
  the ones it was told under before. A backend that dedupes on those ids sees no duplicates.
- **The live cursor does not move.** A replay records no event and leaves the sweep's cursor, failure state and backoff
  as they were, so it can run beside a live sweep.
- **Both traces come with them.** A replay tells the work root, the lifetime trace (its root, step spans and wait spans)
  and any `chunk completed` marker of each chunk that finished in the window, with the ids the sweep uses.
- **Each request is bounded, a range is not.** A request's window is half-open, from `since` up to but not including
  `until`. It must be positive, no wider than `replay_max_window` seconds in the `[tracing]` block, and end at or
  before now; the daemon refuses a wider one with the limit named, and one ending in the future outright. The
  `hub traces replay` and `runner traces replay` commands take any past range and refuse a future `--until` against the command line's own clock before sending any
  window: they read the limit from the daemon and split the range
  into consecutive windows of at most that width, printing a
  line per window to standard error. Replay runs inside each request, so a window takes a while, and a command line
  client waits up to ten minutes for each. If a window fails, whether the daemon refuses it or the request itself fails
  or times out, the command stops and names the window and the `--since` value to resume from; the windows before it are
  told and need not be told again. The resume value is rounded down to the second, and the overlap is deduped by span
  id.
- **`--dry-run` sends nothing.** It reports the steps, chunks, spans and batches the window would tell, and works with
  tracing off, which is a cheap way to size a window. Without `--dry-run`, a hub with tracing off refuses the replay.
  A runner refuses any replay, a dry run too, until its first registration with its hub.

If the exporter refuses a batch, the replay stops and reports what it had told by then.

## Upgrading to hub-minted runner ids

The hub mints each runner's id, `rn_` and a ULID, when an operator adds the runner, and the runner's name becomes a
label beside it. Trace and span ids derive from the chunk, epoch and span role, never from a runner, so none changes,
and the trace schema and scope versions stay `3`. From the upgrade on:

- **`blizzard.runner.id` carries the `rn_` id.** Spans told before the upgrade carry the runner's name there, and a
  backend keeps them as they were told; no replay is needed, and none rewrites them.
- **`blizzard.runner.name` rides beside it** on every span that carries the id: the hub's step spans, the runner's
  lease spans, the harness spans, data points and log records it forwards, and both daemons' platform spans.

To select one runner across the upgrade, match its name in `blizzard.runner.name`, or in `blizzard.runner.id` on a span
that has no `blizzard.runner.name`. A hub replay of a window from before the upgrade tells its steps again under the
same span ids, now with the `rn_` id and the name, and a runner replay tells a lease minted before the upgrade under
the id of the runner's current registration, the one the hub's spans carry; a backend that dedupes on span ids keeps
whichever it was told first. An upgraded runner tells nothing until its first registration with the upgraded hub, then
tells the leases that closed meanwhile.

## Upgrading from trace schema 2

Trace schema 3 splits each chunk into a work trace and a lifetime trace. The work trace keeps the trace id, and its root
span keeps its span id but is renamed `chunk work` and now starts at the first step. The name `chunk` now belongs to the
lifetime trace's root, a different span in a different trace, so a dashboard or alert that selects the span named
`chunk` must select `chunk work` to keep reading the work root. The backlog, escalation and pause waits and the
completion marker leave the work trace for the lifetime trace, under new ids. A backend that dedupes on span ids would
keep the old `chunk` span in place of the new `chunk work` one, and the old waits would sit beside the chunk's steps, so
the hub's data is rebuilt rather than mixed. A runner's data is unchanged.

1. Redeploy the hub on the release that carries schema 3. Runner data needs no replay, its spans keeping their ids and
   parents; redeploy the runners too, so their spans carry the same schema and scope version as the hub's in a work
   trace.
2. Delete the hub's dataset (`blizzard-hub` unless `OTEL_SERVICE_NAME` names another), keeping the environment and its
   ingest keys. Leave the runner, CLI and worker-program datasets alone. The replay creates the lifetime spans' dataset,
   `blizzard-chunk`, when the first one arrives.
3. Tell the hub's history again with `blizzard hub traces replay --since <t> --until <now>`; the replay tells both
   traces of every chunk that finished in the window. No runner replay is needed. For the full history, pass a `--since`
   earlier than the fleet's first chunk.

Platform spans the hub told live are not stored, so no replay brings back those deleted with the dataset.

## Upgrading from trace schema 1

Trace schema 2 put every span of a chunk in one trace, so every trace id changed. Spans told under schema 1 keep their
old trace ids and would sit beside the new ones as separate traces, so the backend is rebuilt rather than mixed.

1. Redeploy the hub and every runner on the release that carries schema 3 or a later one.
2. Drop or segregate the schema 1 data however your backend allows. A backend with datasets: delete the datasets the
   fleet wrote (`blizzard-hub`, `blizzard-runner`, `blizzard-cli`, `blizzard-worker-program`, and the dataset of every
   service name `[tracing.worker_program_services]` maps a program to), keeping the environment and its ingest keys.
3. Tell the history again. Run `blizzard hub traces replay --since <t> --until <now>`, then
   `blizzard runner traces replay --dir <runner dir> --since <t> --until <now>` for each runner's store. For the full
   history, pass a `--since` earlier than the fleet's first chunk, such as the date the fleet started; a window with
   nothing in it costs one quick request, and `replay_max_window` is a week by default.

Platform, worker CLI, operator CLI and worker-program spans are sent live and never stored, so no replay brings them
back. Their schema 1 spans cannot be told again; they start again from the deploy. The replay tells the chunks in the
shape of the running release, so a fleet coming from schema 1 replays once and skips the schema 2 note.
