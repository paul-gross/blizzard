/**
 * Backstop `refetchInterval` for the panel's queries whose data is now kept current
 * by the runner's own SSE stream (blizzard#317 Phase 4) — mirrors `fleet`'s
 * `LIVE_COVERED_POLL_BACKSTOP_MS` (issue #316) in intent, but not in value: the
 * runner's stream carries no keepalive-adjacent guarantee the hub's board relies on
 * to keep 45s tight, and this panel is a single machine-local operator surface, not a
 * shared board — so the floor here is deliberately coarser than 45s, but not as coarse
 * as a first pass chose. 1 minute, not 5: `leases.query.ts` feeds
 * `local-heartbeat-freshness`'s own decay curve, whose documented checkpoint is
 * "≈50% at one minute" — a slower backstop leaves that rendering stale relative to its
 * own claim for most of a healthy node-step's life (the elapsed-time-derived state a
 * heartbeat *is* rides no event at all, D7, so this interval is the only thing that
 * ever refreshes it), and `status.query.ts` feeds the dashboard's `runner` section
 * (the daemon's own tick beat, also D7-silent, and the hub-pause mirror, which no kind
 * in the vocabulary represents either). Still a real backstop, not a return to
 * per-surface polling: 1 request/minute across two reads is negligible idle volume
 * even left running for a full shift, and every transition either read renders that
 * *does* carry a cause is still SSE-driven, not floor-driven.
 *
 * The bound this leaves for what it doesn't cover: an elapsed-time-derived rendering
 * fed by this floor can read up to one interval behind the truth between refreshes,
 * never fresher — its anchor only moves when this backstop (or a covering event)
 * lands. Every reader of this constant that carries that caveat should point here
 * rather than restate it (`bzh:one-prose-home`). The same bound covers a *read*-time,
 * not elapsed-time, derivation too: `leases.query.ts`'s `LeaseActivity.state` computes
 * its `"exited"` branch from a live process-alive probe, not from a stored fact, so no
 * lease-changed cause announces a worker's pid dying — that transition surfaces only
 * once this backstop (or an unrelated lease-changed frame for the same lease) triggers
 * the next read, up to one interval later, until REAP's own closure catches up and
 * publishes lease-changed(reaped) for real (blizzard#317 review round 4, F3).
 */
export const RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS = 60_000;
