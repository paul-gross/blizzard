# Watching the fleet — the event log and kiosk mode

## Operational visibility — the event log

The failures that cost the most are the least visible: a worker that exits without recording a completion and leaves its
chunk sitting `running` behind a dead process, a spawn/push/declare command that failed on a missing environment var, a
stall past the liveness window. A chunk's *status* says it is stuck; it does not say *why*. The hub owns a durable,
append-only, typed and **severity-ranked** operational event log that does.

- **The runner reports failure events.** When a worker exits non-clean, when a captured spawn/push/environment-prep
  command fails, or when an attempt is reaped/abandoned/escalated, the runner emits an operational event on the same
  durable store-and-forward path completions ride. The hub folds each into the log. Severities are `info`, `warning`,
  and `critical` — a **closed** set, because the feed ranks by it and orders anything else below every other row.
  Runner-emitted examples: `info` (an attempt given up because the chunk moved on), `warning` (an attempt failed and
  will retry, or a command failed), `critical` (a worker lost to a human, retries exhausted).
- **The hub emits its own events too**, for the failures only it sees. Chief among them, `hub-node-unroutable-outcome`
  (`critical`): a hub command node produced an outcome its graph authors no edge for, so nothing routes and the chunk
  re-polls that same outcome **forever**. It is announced once per node visit, not once per poll. The remedy is not a
  retry — author the missing edge on the graph, then requeue. The work-item closure events (`work-item-closed`,
  `work-item-close-failed`) are the hub's too — see
  [Closing delivered work items](./work-sources.md#closing-delivered-work-items).
- **`GET /api/events`** returns the log newest-and-most-severe first, filterable by `severity` / `runner_id` /
  `chunk_id` / `since`, with a bounded default page. Existing escalations appear in the *same* feed as a `needs-human`
  event kind — `needs_human` is one row in one surface, not a place to look separately. A row leaves the feed when its
  escalation is superseded, which any of four things does: a requeue, an operator `chunk restart`, the next attempt's
  lease, or the chunk ending — stopped or done.
- **The board's Events tab** renders the feed live: new events fan out over the existing SSE spine
  (`/api/events/stream`), so an open board updates without polling. Each row links to its chunk.
- **`GET /api/activity`** is a second, differently-shaped operator read: the board's Event log rail backfills from it on
  page load rather than starting empty and waiting for live traffic. It merges three already-durable sources —
  chunk-level status changes, the operational event log above, and runner pause/resume — newest-first, bounded by
  `since` (default 24h before now) and `limit` (default/max 200, `1..1000`), gated the same as `GET /api/events`.
  Ordering is pure recency, not severity-then-recency: this is a recent-activity feed, not the operational log's triage
  view. After the initial backfill the rail continues live over the same `/api/events/stream`, deduped against the
  backfilled rows by each frame's fact-identity `key` rather than by timestamp.

The event log makes failures **visible in-product**; it does not repair the underlying failure modes (a missing
spawn-env var, a `SessionEnd` hook that never fired) — those are fixed at their source. It is append-only with no
rotation policy beyond that.

## Kiosk demo mode — an unattended board on a wall screen

`?demo=true` on any board URL hands the board to an automatic tour, for a screen left running in a room rather than
watched by an operator. Nothing in the UI announces it and nothing links to it — the query string is the whole switch.

```text
https://hub.example.com/board?demo=true
```

One cycle, repeated forever: a chunk is picked at random off the live fleet, opened on the board, and its detail dock
scrolled slowly to the bottom; the tour then descends into that chunk's Artifacts tab and shows a random artifact at a
time, each scrolled top to bottom across its dwell; at the end of the cycle it swaps to another chunk. A chunk with no
artifacts ends its cycle early rather than holding an empty viewer.

Four dials tune it. Each takes a bare number of **seconds**, or a number with an `s` / `m` / `h` suffix — `900`, `15m`,
and `0.25h` all mean the same thing. A value that cannot be read falls back to its default rather than producing a cycle
of no length.

| Param                      | Default | Controls                                                                                                                                                                |
| -------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `demo_swap_chunk_interval` | `2m`    | One whole cycle — the board dwell plus the artifact tour — before swapping chunks.                                                                                      |
| `demo_board_scroll`        | `60s`   | How long the board's detail dock takes to scroll to its bottom. Clamped to **half** `demo_swap_chunk_interval`, so the artifact tour always keeps a share of the cycle. |
| `demo_artifact_interval`   | `20s`   | How long each artifact holds the screen, and so how long its scroll takes.                                                                                              |
| `demo_reload_after`        | `1h`    | Reload the page once it has been up this long. `0` disables it.                                                                                                         |

Two behaviors exist for the wall-screen case specifically, and are worth knowing before you blame the display:

- **The screen is held awake** via the Screen Wake Lock API, re-acquired whenever the tab becomes visible again (the
  browser drops the lock every time it is hidden). It needs a **secure context**, so a hub reached over plain HTTP falls
  back to the display's own idle timer. This is the usual reason a kiosk still blanks.
- **A redeploy is picked up automatically.** A single-page app fetched once otherwise runs its original bundle forever.
  The tour re-reads `index.html` past the HTTP cache at each chunk swap and reloads when the deployed document has
  changed; `demo_reload_after` is a backstop under that for what no deploy fixes. Both reloads happen **between**
  chunks, never mid-scroll, and the demo params ride the URL, so what comes back up is the tour rather than a plain
  board.

Demo mode needs the same session any board does — it drives real reads, so it will not run for a signed-out or
permissionless viewer (see [Human authentication](./human-auth.md)). It only reads: it scrolls and navigates, and never
activates an operator control, so a board left touring cannot pause a runner or answer an ask.
