/**
 * The TanStack Query keys the fleet reads under, in one place so the live-update
 * service ({@link ./sse/fleet-live}) and the queries agree on what an SSE event
 * invalidates. Every key is namespaced under `hub` so a blanket
 * gap-recovery invalidation after a reconnect can target the whole tree.
 */
export const hubHealthKey = ['hub', 'health'] as const;
export const hubChunksKey = ['hub', 'chunks'] as const;
export const hubQueueKey = ['hub', 'queue'] as const;
export const hubRunnersKey = ['hub', 'runners'] as const;
export const hubQuestionsKey = ['hub', 'questions'] as const;
/** The fleet spend-since read's key prefix (issue #60) — the actual query key appends
 * the `since` instant, so an SSE invalidation naming just this prefix closes every
 * cached window at once (TanStack's default prefix match on `invalidateQueries`). */
export const hubFleetSpendKey = ['hub', 'fleet-spend'] as const;
export const hubGraphsKey = ['hub', 'graphs'] as const;
/** The resolved-identity read (issue #93) — `GET /api/me`. Never invalidated by an
 * SSE event (no event names an identity change yet, #94); the login/logout flows
 * invalidate it explicitly instead. */
export const hubMeKey = ['hub', 'me'] as const;
/** The configured login-provider list (issue #93) — `GET /api/auth/providers`. */
export const hubAuthProvidersKey = ['hub', 'auth', 'providers'] as const;

/** One chunk's full aggregate, keyed by id. */
export function hubChunkKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId];
}

/** One chunk's related PM items (issue body + comments), keyed by id. */
export function hubChunkPmItemsKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId, 'pm-items'];
}

/** One minted graph's full structure, keyed by id. */
export function hubGraphKey(graphId: string | null): readonly unknown[] {
  return ['hub', 'graph', graphId];
}
