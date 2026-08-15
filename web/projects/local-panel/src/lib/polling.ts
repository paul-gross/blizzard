/**
 * Backstop `refetchInterval` for the panel's queries whose data is now kept current
 * by the runner's own SSE stream (blizzard#317 Phase 4) — mirrors `fleet`'s
 * `LIVE_COVERED_POLL_BACKSTOP_MS` (issue #316) in intent, but not in value: the
 * runner's stream carries no keepalive-adjacent guarantee the hub's board relies on
 * to keep 45s tight, and this panel is a single machine-local operator surface, not a
 * shared board — so the floor here is deliberately coarser. 5 minutes: long enough
 * that it is negligible idle request volume even left running for a full shift, short
 * enough that a dropped frame or a missed reconnect still self-heals well within an
 * operator's attention span.
 */
export const RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS = 5 * 60_000;
