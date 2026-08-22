# Observability

## The operational event log

The hub owns a durable, append-only, typed, severity-ranked operational event log — the place that says why a chunk is
stuck when its status only says that it is. Severities are a closed set — info, warning, critical — because the feed
ranks by severity, ordering anything else below every row. The log has no rotation policy.

The runner emits failure events on the same durable store-and-forward path completions ride — non-clean worker exits,
failed captured spawn, push, or environment-prep commands, reaped, abandoned, or escalated attempts — and the hub folds
each into the log. The hub emits its own events for failures only it sees, chief among them
`hub-node-unroutable-outcome` (critical): a hub command node produced an outcome with no authored edge, so nothing
routes and the chunk re-polls it forever; announced once per node visit, not per poll — the remedy is authoring the
missing edge then requeuing, not retrying. The work-item closure events are also the hub's —
[work-sources.md](./work-sources.md) owns them.

Escalations appear in the same feed as a needs-human event kind — one row, one surface; a row leaves when its escalation
is superseded by any of a requeue, an operator `chunk restart`, the next attempt's lease, or the chunk ending `stopped`
or `done`.

## Reading the feed

`GET /api/events` returns the log newest-and-most-severe first, filterable by severity, runner_id, chunk_id, and since,
with a bounded default page. The board's Events tab renders the feed live over the SSE spine (`/api/events/stream`),
each row linking to its chunk.

`GET /api/activity` is a second read the board's Event log rail backfills from on page load, merging three durable
sources — chunk status changes, the event log, and runner pause/resume — newest-first, bounded by `since` (default 24
hours back) and `limit` (default and max 200), gated like `GET /api/events`. Activity orders by pure recency, the event
log being the triage view; after backfill the rail continues live over the same stream, deduped by each frame's
fact-identity key rather than by timestamp.

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
