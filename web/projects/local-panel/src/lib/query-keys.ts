/**
 * The TanStack Query keys the local panel reads under, in one place — mirrors
 * `fleet`'s `hub`-namespaced `query-keys.ts` (the fleet/local split: local
 * pages own their own keys). Every key is namespaced under `runner` so it can
 * never collide with a `hub`-namespaced key from the shared `fleet` library.
 */
export const runnerLeasesKey = ['runner', 'leases'] as const;

/**
 * `GET /api/dashboard` — the panel's seven status reads composed into one response
 * (issue #311): `runner` (identity, capacities, hub connectivity, last tick),
 * `environments`, `asks`, `escalations`, `takeovers`, `facts`, and `fleet_summary`.
 * One key for every rail this panel polls, so TanStack dedupes the N components
 * that inject it into the single shared `GET /api/dashboard` request — the same
 * dedupe `local-info.ts` and `local-panel-mobile.ts` already relied on for their
 * one shared `GET /api/runner` read, now extended to all seven sections.
 */
export const runnerDashboardKey = ['runner', 'dashboard'] as const;

/** `GET /api/auth/session` — whether the surface is gated, and the signed-in
 * hub username, behind the panel's username/logout control (issue #129). */
export const runnerSessionKey = ['runner', 'session'] as const;

/**
 * One chunk's pass-through work items (issue title + labels), keyed by chunk id.
 * Deliberately its own key — never invalidated or refetched by the leases poll
 * (issue #28's severable title enrichment) — so a distinct `chunk_id` here can
 * never collide with `hub`-namespaced `chunk-work-items` reads in `fleet`.
 */
export function runnerChunkWorkItemsKey(chunkId: string): readonly unknown[] {
  return ['runner', 'chunk', chunkId, 'work-items'];
}

/**
 * One chunk's work items for the chunk detail route's Issues section (issue
 * #318) — full-fidelity, not the severable row-decoration read above: this
 * page renders a real loading/error/empty triad for it, so it needs a real
 * fetch (retried, not silently swallowed after one attempt) rather than the
 * list rows' single-shot decoration. Its own key so it shares neither cache
 * entry nor observer options with {@link runnerChunkWorkItemsKey}.
 */
export function runnerChunkWorkItemsDetailKey(chunkId: string): readonly unknown[] {
  return ['runner', 'chunk', chunkId, 'work-items-detail'];
}

/**
 * One lease's transcript read (issue #29), keyed by lease id — switching the
 * selected row is a distinct cache entry, never invalidated by the leases poll.
 */
export function runnerTranscriptKey(leaseId: string): readonly unknown[] {
  return ['runner', 'lease', leaseId, 'transcript'];
}

/**
 * One chunk's full detail aggregate (issue #185) — the chunk-detail dock's
 * header, pass-through-forwarded to the hub (`ChunkDetail.pause` is the only
 * way this panel learns a chunk is paused). Its own key, keyed by chunk id,
 * distinct from the severable {@link runnerChunkWorkItemsKey} title read and
 * from `hub`-namespaced `chunk` reads in `fleet`.
 */
export function runnerChunkDetailKey(chunkId: string): readonly unknown[] {
  return ['runner', 'chunk', chunkId, 'detail'];
}
