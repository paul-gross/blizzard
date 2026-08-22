# Transcript lanes — the warn lane and the outbound lane

## Warning on a worker's session context — the warn lane (off by default)

A graph's `rotate` bounds are evaluated when a node-step is about to *start*, against the session it would resume. That
leaves the inside of a long-running invocation unobserved, which is where a session's context actually grows. The warn
lane samples a **running** lease's session context on a cadence and reports the first time it crosses a line you set:

```toml
[context]
warn_tokens = 300000
sample_interval_seconds = 60
```

- **Absent `warn_tokens`, the lane is off entirely** — no transcript is read at all, so a runner that never opts in pays
  nothing for it. `sample_interval_seconds` alone does not turn it on; it is inert until `warn_tokens` is set.
- It **observes, and gates nothing.** No worker is stopped, no chunk is parked, and no session is rotated by anything
  here — that decision belongs to a graph's `rotate` block. The spend controls that *do* intervene are
  [`[cost]`](./spend.md).
- The warning fires **once per lease**, on the first crossing, not once per sample. A session past the line goes on
  being sampled without re-reporting.
- The measurement is the conversation's size — the prompt size of the session's newest turn, what a resume of it would
  pay for again — not tokens accumulated across the run.

A crossing surfaces as a `worker-context-warned` entry in the operational event feed — the board's event log, and
[`GET /api/events`](./observability.md#operational-visibility--the-event-log) — carrying the lease, node, measured
context, and the configured line. The runner also logs it locally at WARNING. Unlike `[external_subscription_usage]`,
this lane ships no read-only probe verb: to confirm it is sampling before waiting on a crossing, read the runner's
journal, or the `context_samples` table in `runner.db` directly.

## Shipping transcript content to the hub — the outbound lane (off by default)

Alongside the fact lane (`lease.minted`, completions, and the rest), the runner carries a second, structurally
independent outbound lane for transcript content: normalized turns read from the harness's own session transcript,
sliced into turn-range records and pushed to the hub over their own route, buffering through a hub outage exactly like
the fact lane's own store-and-forward does. A wedged or slow transcript FLUSH never delays a completion or a gate
decision — the two lanes share nothing but the runner process. The hub stores what it accepts, compressed at rest,
behind an operator-only read API (blizzard#247) — this is a durable transcript, not a discard sink.

The one exception is the READ half, not the flush: a closing lease's own still-open segment is pumped one last time
before the closure it gates is recorded, bounded by a `PUMP_LEASE_MAX_SECONDS` (5s) budget checked only between reads —
so one slow in-flight read can still push closure past that bound, and a raised exception is isolated (never fails the
closure) but not free of delay either. This is deliberate: draining what a closing segment can before it stops being
pumpable is worth a bounded wait, not zero delay.

```toml
[transcripts]
# Ship transcript records to the hub. Off by default: with no [transcripts] table (or
# `ship` omitted), the runner reads no session content and enqueues no record. The switch
# gates non-final records alone — every open segment still ships its own final record at
# lease closure regardless, the same unconditional close-out the fact lane's own facts
# get. A segment that never had a single successful read is no exception: a normalizer
# version is a static per-harness constant, so its final record declares the source's own
# "never ran" sentinel rather than shipping nothing.
ship = false
```

- **`ship = false` does not mean the lane is silent.** Every closed lease still enqueues and attempts to flush its final
  marker regardless of `ship` (above). Against a hub that does not yet serve `/api/fleet/transcripts` — an older hub
  version, most commonly — that flush fails every tick: the marker buffers forever (never draining) and a transport
  error logs each attempt. This is the same store-and-forward behavior the fact lane already has against any unreachable
  route; it is not a data-loss risk, but "off by default" alone does not prepare an operator to expect it. And once that
  flush *does* succeed — a hub new enough to accept it — the final marker still lands in the hub's segment index even
  with `ship = false`: a content-free row (`turn_range_start=0, turn_range_end=-1,
  turns=[], truncated=false`) per
  chunk closure. Its presence alone does not mean real transcript content was captured; it means only that the lease
  closed.
- **Capped at both ends, independently.** The runner enforces its own 8 MB per-record cap and 64 MB per-chunk budget as
  the well-behaved case (D4): an oversized turn's own text, its tool call's output, its tool call's input (any nested
  sidechain turn's, too), are all shrunk in place rather than dropped, so the runner's read position still advances past
  it. A record still over cap once nothing is left to shrink — structural overhead alone — ships instead as an
  empty-turns slice over the same claimed range, never as an over-cap body. Past the 64 MB chunk budget, the runner
  stops shipping that chunk's content but still ships every open segment's final record. The hub enforces its own,
  independent caps as the rogue case — 10 MB/record, 64 MB/chunk, 2 GB/runner/day — rejecting an over-cap record but
  still acknowledging it, so a misbehaving runner wastes its own budget without wedging its lane. The hub's per-record
  cap sits deliberately *above* the runner's: over the runner's, content is shrunk and every turn survives; over the
  hub's, the whole record's turns are rejected and stored as `[]`. Every runner-side outcome above is recorded on the
  segment and surfaced as a `warning` operational event (see
  [the event log](./observability.md#operational-visibility--the-event-log) below), the same way a captured command
  failure is.
- **Every ceiling above is a default, not a fixed limit.** Each is overridable under `[transcripts]` in the owning
  daemon's config — `record_max_bytes` and `chunk_max_bytes` on the runner, `record_max_bytes`, `chunk_budget_max_bytes`
  and `runner_daily_rate_max_bytes` on the hub. Widen them for a backfill window — `blizzard runner transcript reship`
  spends the per-chunk budget a second time over the same chunk — then restore them, keeping the runner's per-record cap
  at or below the hub's so the ordering above still holds. **A daemon resolves its ceilings once, at startup**: edit the
  file, then restart that daemon, or the old values stay in force with nothing in the output saying so. A bad value
  fails loud rather than falling back to the default — a non-integer or non-positive cap is a config error, and on the
  hub that fails the `migrate` step the unit runs before `host`, so the daemon does not come up. Two limits widening
  does *not* lift: a proxy in front of the hub rejects an oversized body before ingest ever adjudicates it (raise
  [`client_max_body_size`](./human-auth.md#behind-a-tls-terminating-reverse-proxy) with the hub's record cap), and a
  chunk whose shipping already stopped stays stopped — the per-chunk budget latches on the segment, so widening frees
  the next segment, never the stopped one.
- **Late links, for content whose call shipped earlier.** The runner reads a session in windows, so a tool's result and
  a subagent's conversation routinely arrive in a later record than the call that produced them. Both ship carrying the
  `tool_use_id` of that call — a result as a `tool` turn flagged `output_patch`, a conversation as a top-level
  `sidechain` turn naming `parent_tool_use_id` — and the board folds each back onto its call when rendering. Reading the
  API directly, expect to see them unmerged; a late turn whose call is outside the segment you fetched stays standalone
  rather than being dropped. A conversation whose spawning call the runner never observed at all remains genuinely
  unlinkable: it is dropped, and says so as a `warning` event naming the segment.
- **Reads are operator-only.** A transcript holds everything a worker saw; reading one back
  (`GET /api/chunks/{id}/transcripts` and its per-segment content route) requires the `transcript:read` permission,
  `contributor` role and above — a runner's own fleet-plane token can push to the ingest route but can never read one
  back.
- **History predating the lane is imported by hand**, with `blizzard runner transcript
  backfill` (blizzard#250). It
  walks this runner's own lease records — never a sweep of the harness directory, which on a working machine is mostly
  the operator's own sessions — opens a segment per session whose file it can still read, and enqueues it onto the same
  outbound lane as an ordinary segment. Rerunnable by design: a session that already holds a segment is skipped, and a
  session this run could not finish stays open for the next run to resume rather than being closed out, so a rerun costs
  the hub a duplicate turn range at worst — its natural key discards that.

  **Run it as the runner's own user, with the runner's own environment, and with the daemon stopped.** All three are
  load-bearing under the unit this document prescribes (`User=blizzard`, `EnvironmentFile=`, `--dir`), and only the last
  is enforced:

  - **Daemon stopped** — the verb writes the store, which is single-writer. It refuses while anything holds the
    runtime's socket, and fails *closed*: a daemon that is wedged or answers unhealthily still counts as holding it.
  - **The runner's user** — with no `transcripts_root` set, transcripts are read from `$HOME/.claude/projects`. Run as
    anyone else and every session reads as *gone*.
  - **The runner's environment** — the hub token comes from the process environment. Without it every flush is refused,
    and the sessions stay buffered rather than reaching the hub.

  `--dry-run` classifies without opening, draining or shipping anything; its counts are what a real run would *attempt*,
  not a promise (a real run flushes between sessions, so backpressure moves the line). `--limit N` bounds one run —
  worth using on a long history, since the hub enforces a per-runner daily rate (2 GB by default, widenable via
  `runner_daily_rate_max_bytes`) and content past it is rejected rather than queued. The verb refuses outright while
  `ship = false`.

  The counts mean: *imported* — read and enqueued to the outbound lane, which is not the same as accepted by the hub;
  *capped* — the subset of those the hub refused or the runner stopped shipping, reported separately for exactly that
  reason; *gone* — no readable transcript found for that session by **this run** (usually a file the harness rotated
  away, but a wrong root or user reads identically, and nothing is written either way, so a rerun retries it);
  *deferred* — left for a rerun, whether by `--limit`, by outbound backpressure, or because the session could not be
  read to its end this time.

  One imperfection is accepted rather than worked around: a pre-lane session resumed in place recorded no resume
  offsets, so it imports as one merged segment attributed to the lease its session began on, rather than splitting at
  its resume seams.

- **A segment already shipped can be sent again**, with `blizzard runner transcript reship
  SEGMENT_ID`. It is for a
  segment the hub holds in a form this runner has since outgrown — most often one an older, smaller per-record cap
  shrank. It carries **the same three run conditions as `backfill` above** (runner's user, runner's environment, daemon
  stopped), enforced by the same refusal.

  **It supersedes rather than corrects, and the duplicate is the mechanism, not a bug.** The hub's ingest is idempotent
  on `(segment_id, turn_range_start)` and never overwrites a record it already accepted, so a short segment cannot be
  fixed in place — only followed by a second one carrying the same lease's content. Both then stand: the chunk board's
  Transcripts tab lists **two** segments for that lease, and the source is deliberately left exactly as it shipped so
  the record of what the hub was once told stays honest. Decide that is what you want before running it.

  A rerun resumes the same superseding segment rather than opening a third; the verb refuses a segment whose lease is
  still active, because a live lease's segment belongs to the running pump. Three warnings can follow the report line,
  and each means something different: *shipping is stopped for this chunk* — nothing was sent at all, and note that a
  re-ship spends the per-chunk budget (64 MB by default) a **second** time; *the source was not read to its end* — the
  new segment stays open, rerun to resume it; *the new segment is itself marked truncated* — check the reason in the
  event log, since only some reasons are about the cap.
