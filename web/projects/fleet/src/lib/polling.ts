/**
 * Backstop `refetchInterval` for hub queries whose data is already kept current by a
 * live SSE event (issue #316) — the SSE spine covers these reads (see
 * `EVENT_INVALIDATION_REGISTRY` in `./sse/fleet-live.ts`) and reconnect gap recovery
 * closes any missed window, so the floor is no longer the primary freshness
 * mechanism, just insurance against a dropped frame. 45s: long enough that it is a
 * negligible share of idle request volume, short enough that a silent gap still
 * self-heals within roughly a minute.
 */
export const LIVE_COVERED_POLL_BACKSTOP_MS = 45_000;
