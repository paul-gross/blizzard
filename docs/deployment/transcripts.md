# Transcripts

## The transcript lane

The runner carries a second, structurally independent outbound lane for transcript content: normalized turns from the
harness's own session transcript, sliced into turn-range records, pushed over their own route, buffered through a hub
outage like the fact lane's store-and-forward — a wedged or slow flush never delays a completion or gate decision. One
read-side coupling remains: a closing lease's still-open segment is pumped once more before the closure records, bounded
by a five-second budget checked only between reads — one slow read can push closure past it, a deliberate bounded wait.

`[transcripts]` `ship` defaults to false: with no table or `ship` omitted, the runner reads no session content and
enqueues no non-final record. `ship` gates non-final records alone: every open segment still ships its final record at
lease closure — the same unconditional close-out facts get — and a segment with no successful read declares the
normalizer's never-ran sentinel rather than shipping nothing. Once a hub accepts it, the final marker lands in the
segment index even with `ship = false` — one content-free row per chunk closure, whose presence means only that the
lease closed, not that content was captured.

`ship = false` is therefore not silent: against a hub without `/api/fleet/transcripts` (most commonly an older hub) the
final-marker flush fails every tick, buffering forever with a transport error logged per attempt — not data loss, but
expect it.

The hub stores accepted content compressed at rest behind an operator-only read API — a durable record, not a discard
sink. Reads through the hub's API are operator-only, requiring `transcript:read` (contributor and above); a runner's
fleet token can push to ingest but never read back through it. A runner's own local panel is a separate case: it reads
its own chunk-scoped segments straight from that runner's local store, through the runner daemon's own
`GET /api/chunks/{chunk_id}/transcripts[/{segment_id}]` pair (`src/blizzard/runner/api/transcript_segments.py`) — a
runner-local read never routed through the hub or gated by `transcript:read`, since it never crosses the fleet token
boundary the hub's API guards.

Tool results and subagent conversations routinely arrive in later records than the call that produced them (the runner
reads a session in windows), shipping with that call's `tool_use_id`; the board folds them back on render, direct API
reads see them unmerged but never dropped, and a conversation whose spawning call was never observed is genuinely
unlinkable — dropped, with a warning event naming the segment.

## Caps

Caps apply at both ends independently; the runner's are the well-behaved case: under the 8 MB record cap oversized turn
text, tool output, and tool input (nested sidechains too) shrink in place rather than drop, so the read position
advances; a record over cap with nothing left to shrink ships as an empty-turns slice over the claimed range, never an
over-cap body. Past its 64 MB per-chunk budget the runner stops shipping the chunk's content but still ships every open
segment's final record. Every runner-side capping outcome is recorded on the segment and surfaced as a warning
operational event.

The hub's caps are the rogue case — 10 MB per record, 64 MB per chunk, 2 GB per runner per day — rejecting an over-cap
record but still acknowledging it, so a misbehaving runner wastes its own budget without wedging its lane. The hub's
per-record cap sits deliberately above the runner's: over the runner's, content shrinks and every turn survives; over
the hub's, the record's turns are rejected and stored empty.

Every ceiling is an overridable default under the owning daemon's `[transcripts]` — runner: `record_max_bytes`,
`chunk_max_bytes`; hub: `record_max_bytes`, `chunk_budget_max_bytes`, `runner_daily_rate_max_bytes`; widen for a
backfill window then restore, keeping the runner's record cap at or below the hub's so the ordering holds. Ceilings
resolve once at startup: edit then restart that daemon, or the old values silently stay in force; a non-integer or
non-positive cap fails loud as a config error — on the hub at the migrate step, so the daemon never comes up.

Widening lifts neither of two limits: a proxy still rejects an oversized body first (raise `client_max_body_size` with
the hub's record cap — [human-auth.md](./human-auth.md)), and a stopped chunk stays stopped — the per-chunk budget
latches on the segment, freeing the next segment, never the stopped one.

## Backfill

`blizzard runner transcript backfill` imports pre-lane history: it walks this runner's own lease records — never the
harness directory, mostly the operator's own sessions — opening a segment per still-readable session file onto the
ordinary outbound lane. One accepted imperfection: a pre-lane session resumed in place recorded no resume offsets and
imports as one merged segment on the lease it began, not split at resume seams.

Run backfill as the runner's own user, with the runner's environment, daemon stopped — all three load-bearing, only the
last enforced: it writes the single-writer store, refusing while anything holds the socket and failing closed (a wedged
daemon counts); with no `transcripts_root`, transcripts read from `$HOME/.claude/projects`, so another user reads every
session as gone; and the hub token comes from the process environment, without which every flush is refused.

`backfill --limit N` bounds one run — on a long history, content past the hub's per-runner daily rate is rejected rather
than queued; the verb refuses outright while `ship = false`. `backfill --dry-run` classifies without opening or shipping
anything; its counts are what a real run would attempt, not a promise, since a real run flushes between sessions and
backpressure moves the line.

The counts: imported is read-and-enqueued, not hub-accepted; capped is what the hub refused or the runner stopped
shipping; gone is no readable transcript this run — a rotated file, though a wrong root or user reads identically, and a
rerun retries it; deferred is left for a rerun by `--limit`, backpressure, or an unfinished read.

Backfill is rerunnable: a session already holding a segment is skipped, an unfinished one stays open for the next run to
resume, and a rerun costs at worst a duplicate turn range the hub's natural key discards.

## Reship

`transcript reship SEGMENT_ID` re-sends a shipped segment the hub holds in an outgrown form — most often one an older,
smaller record cap shrank — under backfill's same three run conditions, enforced by the same refusal.

Reship supersedes rather than corrects — the duplicate is the mechanism: hub ingest is idempotent on (segment_id,
turn_range_start) and never overwrites, so a short segment can only be followed by a second carrying the same lease's
content; both stand, the board's Transcripts tab lists two segments for that lease, and the source stays exactly as
shipped — decide first.

A rerun resumes the same superseding segment rather than opening a third; the verb refuses a segment whose lease is
still active — that segment belongs to the running pump.

Three warnings can follow reship's report: shipping stopped for this chunk — nothing sent, and a re-ship spends the
per-chunk budget a second time; source not read to its end — the segment stays open, rerun to resume; new segment marked
truncated — check the event-log reason, only some reasons being the cap.

## The context warn lane

A graph's `rotate` bounds evaluate only when a node-step starts, leaving the inside of a long invocation unobserved —
where context actually grows; the warn lane samples a running lease's session context on a cadence to cover it. The
measurement is the conversation's size — the newest turn's prompt size, what a resume would repay — not accumulated
tokens.

The warn lane is the `[context]` table in `blizzard-runner.toml` — `warn_tokens` and `sample_interval_seconds`; absent
`warn_tokens` the lane is off entirely and no transcript is read, `sample_interval_seconds` alone being inert.

A crossing surfaces as a worker-context-warned entry in the operational event feed
([observability.md](./observability.md)), carrying lease, node, measured context, and the configured line; the runner
also logs it at WARNING. The warning fires once per lease, on the first crossing, not per sample; a session past the
line keeps being sampled without re-reporting.

The warn lane observes and gates nothing — no worker stopped, no chunk parked, no session rotated (rotation is the
graph's `rotate` block; the intervening spend controls are `[cost]`, [spend.md](./spend.md)). It ships no probe verb;
confirm sampling via the runner's journal or the `context_samples` table in `runner.db`.
