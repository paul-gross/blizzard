/**
 * The TanStack Query keys the local panel reads under, in one place — mirrors
 * `fleet`'s `hub`-namespaced `query-keys.ts` (the fleet/local split: local
 * pages own their own keys). Every key is namespaced under `runner` so it can
 * never collide with a `hub`-namespaced key from the shared `fleet` library.
 */
export const runnerLeasesKey = ['runner', 'leases'] as const;

/** `GET /api/runner` — identity, capacities, hub connectivity, last tick. */
export const runnerStatusKey = ['runner', 'status'] as const;

/** `GET /api/environments` — the held env bindings. */
export const runnerEnvironmentsKey = ['runner', 'environments'] as const;

/** `GET /api/asks?open=true` — the open local asks. */
export const runnerAsksKey = ['runner', 'asks'] as const;

/** `GET /api/escalations` — parked escalations with their resume commands. */
export const runnerEscalationsKey = ['runner', 'escalations'] as const;

/** `GET /api/takeovers` — open operator takeovers. */
export const runnerTakeoversKey = ['runner', 'takeovers'] as const;

/** `GET /api/facts` — the local fact log off the outbound ledger. */
export const runnerFactsKey = ['runner', 'facts'] as const;

/**
 * `GET /api/fleet-summary` — the hub-rail counts strip's four bucket counts
 * (ready/running/waiting/needs), pass-through-forwarded to the hub (issue #76).
 * Its own key, distinct from the hub-free `runner`-namespaced reads: it is the one
 * rail read that depends on hub reachability, and degrades on its own error without
 * disturbing them.
 */
export const runnerFleetSummaryKey = ['runner', 'fleet-summary'] as const;

/**
 * One chunk's pass-through PM items (issue title + labels), keyed by chunk id.
 * Deliberately its own key — never invalidated or refetched by the leases poll
 * (issue #28's severable title enrichment) — so a distinct `chunk_id` here can
 * never collide with `hub`-namespaced `chunk-pm-items` reads in `fleet`.
 */
export function runnerChunkPmItemsKey(chunkId: string): readonly unknown[] {
  return ['runner', 'chunk', chunkId, 'pm-items'];
}

/**
 * One lease's transcript read (issue #29), keyed by lease id — switching the
 * selected row is a distinct cache entry, never invalidated by the leases poll.
 */
export function runnerTranscriptKey(leaseId: string): readonly unknown[] {
  return ['runner', 'lease', leaseId, 'transcript'];
}
