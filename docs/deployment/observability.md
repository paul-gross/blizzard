# Observability

## The operational event log

The hub owns a durable, append-only, typed operational event log — the place that says why a chunk is stuck when its
status only says that it is. Severities are a closed set — info, warning, critical — each row carrying its severity as a
badge. The log has no rotation policy.

The runner emits failure events on the same durable store-and-forward path completions ride — non-clean worker exits,
failed captured spawn, push, or environment-prep commands, reaped, abandoned, or escalated attempts — and the hub folds
each into the log. The hub emits its own events for failures only it sees, chief among them
`hub-node-unroutable-outcome` (critical): a hub node's pass answered a choice no authored edge routes, so nothing routes
the chunk on. The hub records it once per node visit and escalates the chunk at once, cause `unroutable-outcome`, naming
the node and the choice; it never takes the pass again. Only a stored version minted before mint required every edge can
answer one. The remedy is moving the chunk onto a graph that routes the choice. The work-item closure events are also
the hub's — [work-sources.md](./work-sources.md) owns them.

`hub-action-missing` (critical): a chunk stands at a hub node that names no action, one whose stored `run:` command the
upgrade could not carry onto an action. The hub escalates the chunk once, cause `hub-action-missing`, naming the node
and the stored command, whether the chunk reaches the node, a hub-advance poll finds it there, or it stood there when
the hub booted. The hub never runs the command. The remedy is moving the chunk onto a graph whose node names an action,
with `chunk restart --to-graph` ([control-verbs.md](./control-verbs.md#restart)), or stopping it.

`hub-action-overran` (warning): a hub node's action pass ran past `action_pass_seconds`
([install.md](./install.md#configuration-and-credentials)). A pass cancelled at its next wait answered `pending` where
its action can wait, so the node's poll timeout still decides when waiting has gone on too long, and its failure choice
where it cannot. A pass still running thirty seconds past the bound is cut off: its turn ends `cut-off`, nothing routes
the chunk, and it keeps its place for its next turn. A node that keeps reaching the bound is one whose forge is slow or
unreachable; the remedy is that forge, or a larger `action_pass_seconds`.

A visit cut off at its deadline on each of its last three turns escalates, cause `hub-action-stuck`, at the start of its
fourth turn, before the hub takes another pass; a turn a hub restart interrupted neither counts nor resets the three.
The remedy is the forge the action calls, or a larger `action_pass_seconds`, then requeuing.

`flow-landed` (info): a hub step's turn merged a chunk onto a repository branch. The event's detail names the flow, its
repository and branch, the revision, and the chunks the landing carried. It is written in the same transaction as the
landing's marker, so a merge the hub recorded always has one.

`turn-interrupted` (warning): the hub found a turn on a branch's delivery line with no end and nothing running it — at
boot, after the process running it stopped mid-turn by a crash or a shutdown, or in its own sweep, after a turn's task
ended without ending its turn. It ends that turn `interrupted`, once; a second look finds nothing to end. A turn whose
visit took longer to build than its bound is ended with this event too. A chunk still standing at its step keeps its
place in the line, and one that has left it, such as a stopped chunk, leaves the line in the same write. The event's
detail names the turn, its submission, and that withdrawal. Nothing needs doing: the chunk's next pass reads the forge
before it acts, so it finds any merge the interrupted turn had already made.

`repository-unresolved` (critical): a `deliver` step's chunk commits resolve to no single
[repository](./repositories.md) record — none stands for one of them, or several match it. The step is refused before
any command runs and routes its failure choice; the remedy is creating or editing the repository records, then
requeuing.

`repository-not-linked` (critical): a `deliver` step's chunk commits resolve to a repository record of the tenant that
the chunk's project does not link. The step is refused before any command runs and routes its failure choice; the remedy
is linking the repository to the project ([projects.md](./projects.md#links)), then requeuing.

`forge-capability-missing` (warning): a `deliver` step's repository sits on a forge that lacks an operation the step's
action requires. The hub refuses the visit before the action runs, writes a `hub-log.forge-capability-missing` artifact,
and escalates the chunk once, cause `forge-capability-missing`, naming the capability, the repository, and the forge.
The remedy is the forge or the action ([forges.md](./forges.md#a-missing-capability)).

`config-old-field-used` (warning): a write named its forge by a deprecated address field (`forge_api_url`, `api_base`,
or `web_base`) instead of `forge`. The hub records one event per field, naming the record, the field, and the forge it
resolved to, and the write answers with a `Deprecation` header and a warning. No event appears once nothing still sends
an old field ([forges.md](./forges.md#the-old-address-fields)).

`owner-unresolvable` (critical): a runner reached an existing session whose recorded harness owner it cannot resolve
right now. The runner binds every enabled declared harness ([worker-spawn.md](./worker-spawn.md) owns that binding).
**Unknown** means the session was recorded under a harness id this runner build doesn't bind at all — the remedy is to
run a runner version that binds that id, on the runner holding the chunk, never to substitute another harness.
**Unavailable** means a bound harness is missing the specific capability being asked of it. The chunk escalates in place
with no takeover command, since no other runner can dispatch to that exact session either — but once the recorded
harness is resolvable again (that runner build), the operator can take the session over by hand
([chunk-operations/takeover.md](./chunk-operations/takeover.md)) alongside that remedy; either way, clearing the
escalation still takes one of the supersessions below.

`no-acceptable-harness` (critical): distinct from `owner-unresolvable` above in reaching only a fresh mint, with no
existing session to name — every member of the node's acceptable harness set ([worker-spawn.md](./worker-spawn.md) owns
that set and its resolution) is unknown, unavailable, **unhealthy**, or resolves none of the session's model preference.
**Unhealthy** is distinct from unavailable: a bound harness with every capability wired can still fail its own computed
health — a missing binary, an incompatible or unknown observed version, failed authentication, a `config_conflict` (an
ambient setting that defeats the runner's own wiring), an unmapped configured tier, or a recorded selftest failure, all
visible with their cause in this runner's own `GET /runners/{runner_id}/api/harness-health` diagnostics. The
escalation's cause is `no-acceptable-harness`, and its detail names each attempted harness and why it was skipped;
`blizzard runner status` prints each harness's id, availability, and health cause beneath such an escalation. The chunk
escalates in place rather than minting under the runner's default harness; with no session ever spawned, the escalation
carries no takeover command either.

Escalations appear in the same feed as a needs-human event kind — one row, one surface; a row leaves when its escalation
is superseded by any of a requeue, an operator `chunk restart`, the next attempt's lease, or the chunk ending `stopped`
or `done`.

## Worker stdout and stderr

Every worker invocation's raw stdout and stderr are captured to the runner's own runtime directory, under
`worker-stdout/<lease_id>.<generation>.{stdout,stderr}` — one pair of files per spawn or resume attempt. `stdout`
carries the harness result envelope (cost, usage, `subtype`, `is_error`, the final result text); `stderr` is what the
process wrote before a crash, and is what a `worker-lost` event's stderr tail is drawn from. An operator with a lease id
and generation number (both visible on the chunk's own attempt history) can open the file directly — the layout needs no
lookup elsewhere.

These files outlive the lease: releasing an environment does not delete them, so an invocation's envelope stays readable
well after the chunk that spawned it has moved on — the one place to see exactly what a harness returned after something
has gone wrong (a usage-limit hit, an interrupted worker, a premature exit). They are not durable forever, though — a
periodic sweep prunes both streams once they age past `[worker_stdout] retention_days` in `blizzard-host.toml` (default
14 days); a file inside the window is left alone regardless of its lease's own state.

## Worker scratch directory

Every worker session gets a private staging directory, under `worker-tmp/<lease_id>/` in the runner's own runtime
directory, exported into the session as `BLIZZARD_TMPDIR`. Unlike `worker-stdout/`, it does not outlive the lease: the
runner removes it once the lease closes, however that closure happened — a node transition, a reap, an escalation — and
recreates it on every later spawn, resume, or judge of a lease that is still open. A directory left behind by a crash is
swept the next time the runner starts.

## Reading the feed

`GET /api/events` returns the log newest first, filterable by severity, runner_id, chunk_id, and since, with a bounded
default page — the cap keeps the newest rows, whatever their severity. The board's Events tab renders the feed live over
the SSE spine (`/api/events/stream`), each row linking to its chunk. A chunk's page links to its own events, and
`/events?chunk=<id>` deep-links the Events tab filtered to that chunk server-side. `blizzard hub events` reads the same
feed from the command line, with `--severity`, `--runner`, `--chunk`, `--since`, and `--limit` mapping onto the route's
filters and `--json` printing the raw rows, `detail` included. A chunk's timeline interleaves its bounces, with their
cause, and its restarts, with who restarted it and from where to where, among its transitions and migrations in time
order.

`GET /api/activity` is a second read the board's Activity feed rail backfills from on page load, merging durable chunk
occurrences (including distinct claims and node transitions), questions and decisions, event-log rows, and runner
pause/resume facts — newest-first, bounded by `since` (default 24 hours back) and `limit` (default 200, refused past
200), gated like `GET /api/events`. Activity orders by pure recency, the event log being the triage view. The rail
continues live over the same stream: only chunk frames with causes represented in the durable activity read enter the
feed. Lease and usage telemetry still refresh chunk and spend views but do not repeat a transition row. Frames sharing a
fact-identity key occupy one row across event types, replay, and backfill; a claim reads as “claimed” rather than as the
chunk's previous transition.

## List pagination

Every bulk list the hub serves — `GET /api/chunks`, `/api/queue`, `/api/backlog`, `/api/findings`, and
`/api/garden-proposals` — shares one keyset-pagination contract, the same shape `GET /api/analytics/events` already
used: an optional `cursor` and a `limit` (`ge=1, le=1000`, default 200 — an over-ceiling or zero `limit` is refused with
a `422`, never silently clamped). The response is an envelope carrying the page's rows alongside `next_cursor`, which is
`null` exactly on the last page; a caller wanting every row follows it to exhaustion. An undecodable `cursor` is a `422`
naming `"malformed cursor"`. `GET /api/events` and `GET /api/activity` instead cap `limit` at 200 (also their default)
and predate the cursor/`next_cursor` half of the contract — each is its own bounded, recency-ordered window, not a walk
over the full backing set.

`GET /api/chunks?board_window=true` is the board's own read: it omits every `done` chunk that finished more than 48
hours ago unless it is among the 10 most recently finished `done` chunks (per project under `?project=`), and keeps
`stopped` and non-terminal chunks of any age. A windowed page can come back short of `limit`; `next_cursor` is still
`null` exactly on the last page. Without the param the list is unfiltered. An omitted chunk still answers by id at
`GET /api/chunks/{chunk_id}`. `GET /api/chunk-counts` serves the all-time count per derived status, plus a `total`, over
exactly the chunks the unfiltered list pages over — the counts ignore the window.

## The hub's sweeps

The hub keeps its derived state current with six sweeps. Each makes one pass over every tenant on its own timer. A pass
that finds a tenant unchanged since it last converged skips that tenant.

| Sweep              | What a pass does                                                                 | Cadence                                    |
| ------------------ | -------------------------------------------------------------------------------- | ------------------------------------------ |
| `forge_status`     | Projects chunk status onto forge labels ([work-sources.md](./work-sources.md))   | `annotation_interval_seconds`, default 120 |
| `event_derivation` | Derives transcript events from new or changed segments                           | 60 seconds                                 |
| `close_drain`      | Closes the work items of delivered chunks ([work-sources.md](./work-sources.md)) | 60 seconds                                 |
| `tenant_teardown`  | Drains each tenant deletion underway ([tenants.md](./tenants.md))                | 30 seconds                                 |
| `trace_export`     | Exports settled fleet traces, when tracing is on ([tracing.md](./tracing.md))    | `[tracing] sweep_seconds`                  |
| `egress`           | Writes the fact export, when a directory is set ([egress.md](./egress.md))       | `[egress] sweep_seconds`                   |

Each sweep logs under its own logger, and two names differ from the table: `event_derivation` logs as
`blizzard.hub.transcript_events` and `close_drain` as `blizzard.hub.work_closure`. The last segment of the logger name
names each pass's `sweep <name>` span.

### Modes

A `[sweeps.<name>]` table in `blizzard-hub.toml` sets a sweep's `mode`:

```toml
[sweeps.close_drain]
mode = "stepped"
```

`timed`, the default, runs a pass at boot and then on the sweep's timer. `stepped` starts no timer and runs no boot
pass: the sweep passes only when a route below asks it to. Any other value refuses boot with an error naming the key. A
mode set for a sweep this hub does not build, such as `trace_export` with tracing off, changes nothing. A file with no
`[sweeps]` table runs every sweep on its timer.

### Running a pass on request

Two routes run one pass and answer once it has finished, so every write the pass makes is readable when the answer
arrives. Both serve a sweep in either mode, and both refuse a runner's credential.

| Route                                  | Runs                                                                                      | Who may call        |
| -------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------- |
| `POST /api/sweeps/{name}/passes`       | One pass of `forge_status`, `event_derivation` or `close_drain` over the request's tenant | A tenant `admin`    |
| `POST /api/admin/sweeps/{name}/passes` | One pass of any built sweep over every tenant, exactly as a timed pass                    | A hub administrator |

A pass for one tenant reads the same data as a pass over every tenant and acts in its own tenant alone. It skips a
tenant that is not due, just as a timed pass does, and still answers `completed`.

| Answer | Meaning                                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------------------- |
| `200`  | `{started_at, finished_at, outcome, error}`; `outcome` is `completed`, or `failed` with the fault's message in `error` |
| `404`  | No sweep this hub builds goes by `{name}`                                                                              |
| `409`  | `{"code": "sweep_in_progress"}`, answered at once: another pass of the sweep holds the lock this one needs             |
| `422`  | The per-tenant route was asked for `tenant_teardown`, `trace_export` or `egress`, which run over every tenant only     |

A pass for one tenant answers `failed` when its tenant's work raised. A pass over every tenant answers `failed` only
when the pass itself raised, which the log records as `sweep failed`; a fault in one tenant is logged against that
tenant, as on a timed pass.

A pass over every tenant holds its sweep's lock. A pass for one tenant holds its sweep's lock in that tenant alone, so
passes for two tenants run side by side. A timed pass that finds its lock held skips that interval and logs
`sweep pass skipped` with `reason="in progress"`. The locks live in the hub's memory and end with the process.

### Stopping a misbehaving sweep

1. Set the sweep `stepped` in `blizzard-hub.toml` and restart the hub. Its timer no longer starts, and the log shows no
   `sweep pass completed` under its logger.
2. Walk it a pass at a time with `POST /api/admin/sweeps/{name}/passes`, reading the log lines each pass writes between
   calls.
3. Remove the `[sweeps.<name>]` table, or set it `timed`, and restart the hub to put the sweep back on its timer.

## Demo mode

`?demo=true` on any board URL hands the board to an automatic tour for an unattended screen; nothing in the UI announces
or links to it — the query string is the whole switch. Demo mode drives real reads, needing a signed-in, permissioned
session like any board ([human-auth.md](./human-auth.md)); it never activates an operator control, so a touring board
cannot pause a runner or answer an ask.

The tour cycles: a random live chunk opens, its detail dock scrolls slowly to the bottom, its Artifacts tab shows random
artifacts each scrolled across its dwell, then the chunk swaps; a chunk with no artifacts ends its cycle early. Four URL
params tune it — `demo_swap_chunk_interval` (2m, one whole cycle), `demo_board_scroll` (60s, the dock scroll, clamped to
half the swap interval so the artifact tour keeps a share), `demo_artifact_interval` (20s per artifact), and
`demo_reload_after` (1h; 0 disables); each takes bare seconds or an s/m/h suffix, an unreadable value falling back to
its default.

A redeploy is picked up: the tour re-reads `index.html` past the HTTP cache at each swap and reloads on a changed
document, `demo_reload_after` the backstop; both reloads happen between chunks, and the params ride the URL so the tour
comes back. The screen is held awake via the Screen Wake Lock API, re-acquired when the tab becomes visible again; the
lock needs a secure context, so a plain-HTTP hub falls back to the display's own idle timer — the usual reason a kiosk
still blanks.

The shape a chunk's steps take as traces — spans, attributes, ids and what never leaves — is in
[tracing.md](./tracing.md).
