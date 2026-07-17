/**
 * The TanStack Query keys the local panel reads under, in one place — mirrors
 * `fleet`'s `hub`-namespaced `query-keys.ts` (the fleet/local split: local
 * pages own their own keys). Every key is namespaced under `runner` so it can
 * never collide with a `hub`-namespaced key from the shared `fleet` library.
 */
export const runnerLeasesKey = ['runner', 'leases'] as const;

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
