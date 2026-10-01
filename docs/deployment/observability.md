# Observability

## The operational event log

The hub owns a durable, append-only, typed operational event log — the place that says why a chunk is stuck when its
status only says that it is. Severities are a closed set — info, warning, critical — each row carrying its severity as a
badge. The log has no rotation policy.

The runner emits failure events on the same durable store-and-forward path completions ride — non-clean worker exits,
failed captured spawn, push, or environment-prep commands, reaped, abandoned, or escalated attempts — and the hub folds
each into the log. The hub emits its own events for failures only it sees, chief among them
`hub-node-unroutable-outcome` (critical): a hub command node produced an outcome with no authored edge, so nothing
routes and the chunk re-polls it forever; announced once per node visit, not per poll — the remedy is authoring the
missing edge then requeuing, not retrying. The work-item closure events are also the hub's —
[work-sources.md](./work-sources.md) owns them.

`owner-unresolvable` (critical): a runner reached an existing session whose recorded harness owner it cannot resolve
right now. This runner binds only `claude_code` ([worker-spawn.md](./worker-spawn.md) owns that binding). **Unknown**
means the session was recorded under a harness id this runner build doesn't bind at all — the remedy is to run a runner
version that binds that id, on the runner holding the chunk, never to substitute another harness. **Unavailable** means
a bound harness is missing the specific capability being asked of it; the production registry always binds `claude_code`
with every capability, so this can't happen today. The chunk escalates in place with no takeover command, since no other
runner can dispatch to that exact session either — but once the recorded harness is resolvable again (that runner
build), the operator can take the session over by hand ([chunk-operations/takeover.md](./chunk-operations/takeover.md))
alongside that remedy; either way, clearing the escalation still takes one of the supersessions below.

`no-acceptable-harness` (critical): distinct from `owner-unresolvable` above in reaching only a fresh mint, with no
existing session to name — every member of the node's acceptable harness set ([worker-spawn.md](./worker-spawn.md) owns
that set and its resolution) is unknown, unavailable, **unhealthy**, or resolves none of the session's model preference.
**Unhealthy** is distinct from unavailable: a bound harness with every capability wired can still fail its own computed
health — a missing binary, an incompatible or unknown observed version, failed authentication, a `config_conflict` (an
ambient setting that defeats the runner's own wiring), an unmapped configured tier, or a recorded selftest failure, all
visible with their cause in this runner's own `GET /api/harness-health` diagnostics, never in this escalation. The chunk
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
periodic sweep prunes both streams once they age past `[worker_stdout] retention_days` in `blizzard-runner.toml`
(default 14 days); a file inside the window is left alone regardless of its lease's own state.

## Worker scratch directory

Every worker session gets a private staging directory, under `worker-tmp/<lease_id>/` in the runner's own runtime
directory, exported into the session as `BLIZZARD_TMPDIR`. Unlike `worker-stdout/`, it does not outlive the lease: the
runner removes it once the lease closes, however that closure happened — a node transition, a reap, an escalation — and
recreates it on every later spawn, resume, or judge of a lease that is still open. A directory left behind by a crash is
swept the next time the runner starts.

## Reading the feed

`GET /api/events` returns the log newest first, filterable by severity, runner_id, chunk_id, and since, with a bounded
default page — the cap keeps the newest rows, whatever their severity. The board's Events tab renders the feed live over
the SSE spine (`/api/events/stream`), each row linking to its chunk.

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
hours ago, and keeps `stopped` and non-terminal chunks of any age. A windowed page can come back short of `limit`;
`next_cursor` is still `null` exactly on the last page. Without the param the list is unfiltered. An omitted chunk still
answers by id at `GET /api/chunks/{chunk_id}`. `GET /api/chunk-counts` serves the all-time count per derived status,
plus a `total`, over exactly the chunks the unfiltered list pages over — the counts ignore the window.

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
