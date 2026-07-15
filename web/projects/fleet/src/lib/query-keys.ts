/**
 * The TanStack Query keys the fleet reads under, in one place so the live-update
 * service ({@link ./sse/fleet-live}) and the queries agree on what an SSE event
 * invalidates (D-097). Every key is namespaced under `hub` so a blanket
 * gap-recovery invalidation after a reconnect can target the whole tree.
 */
export const hubHealthKey = ['hub', 'health'] as const;
export const hubChunksKey = ['hub', 'chunks'] as const;
export const hubQueueKey = ['hub', 'queue'] as const;
export const hubRunnersKey = ['hub', 'runners'] as const;

/** One chunk's full aggregate, keyed by id. */
export function hubChunkKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId];
}

/** One chunk's related PM items (issue body + comments), keyed by id. */
export function hubChunkPmItemsKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId, 'pm-items'];
}
