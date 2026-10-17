/**
 * Backstop `refetchInterval` for hub queries whose data is already kept current by a
 * live SSE event (see `EVENT_INVALIDATION_REGISTRY`), so the floor is not the
 * primary freshness mechanism, just insurance against a dropped frame. 45s: long enough that it is a
 * negligible share of idle request volume, short enough that a silent gap still
 * self-heals within roughly a minute.
 */
export const LIVE_COVERED_POLL_BACKSTOP_MS = 45_000;

/**
 * Backstop `refetchInterval` for runner queries whose data is kept current by the
 * runner's own SSE stream — the runner-side counterpart of
 * {@link LIVE_COVERED_POLL_BACKSTOP_MS}.
 *
 * The bound this leaves for what it doesn't cover: an elapsed-time-derived rendering
 * fed by this floor can read up to one interval behind the truth between refreshes,
 * never fresher — its anchor only moves when this backstop (or a covering event)
 * lands. Every reader of this constant that carries that caveat should point here
 * rather than restate it (`bzh:one-prose-home`). The same bound covers a *read*-time
 * derivation no event announces (a live probe rather than a stored fact): it surfaces
 * only once this backstop triggers the next read, up to one interval later.
 */
export const RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS = 60_000;
