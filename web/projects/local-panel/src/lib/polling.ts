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
 */
export const RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS = 60_000;
