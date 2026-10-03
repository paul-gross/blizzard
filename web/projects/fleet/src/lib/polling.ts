/**
 * Backstop `refetchInterval` for hub queries whose data is already kept current by a
 * live SSE event — the SSE spine covers these reads (see
 * `EVENT_INVALIDATION_REGISTRY` in `./sse/fleet-live.ts`) and reconnect gap recovery
 * closes any missed window, so the floor is no longer the primary freshness
 * mechanism, just insurance against a dropped frame. 45s: long enough that it is a
 * negligible share of idle request volume, short enough that a silent gap still
 * self-heals within roughly a minute.
 */
export const LIVE_COVERED_POLL_BACKSTOP_MS = 45_000;

/**
 * Backstop `refetchInterval` for runner queries whose data is now kept current
 * by the runner's own SSE stream — mirrors {@link LIVE_COVERED_POLL_BACKSTOP_MS}
 * in intent, but not in value: the runner panel
 * panel is a single machine-local operator surface, not a shared board — so the
 * floor here is deliberately coarser than 45s. 1 minute: `leases.query.ts` feeds
 * `local-heartbeat-freshness`'s own decay curve, which anchors its resolution at this
 * interval — a slower backstop would widen the window that curve
 * renders 100% instead of a real drain, without changing what it is actually able to
 * resolve (the elapsed-time-derived state a heartbeat *is* rides no event at all,
 * so this interval is the only thing that ever refreshes it), and `status.query.ts`
 * feeds the dashboard's `runner` section
 * (the daemon's own tick beat, also silent, and the hub-pause mirror, which no kind
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
 * publishes lease-changed(reaped) for real.
 */
export const RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS = 60_000;
