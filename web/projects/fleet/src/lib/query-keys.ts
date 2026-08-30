/**
 * The TanStack Query keys the fleet reads under, in one place so the live-update
 * service ({@link ./sse/fleet-live}) and the queries agree on what an SSE event
 * invalidates. Every key is namespaced under `hub` so a blanket
 * gap-recovery invalidation after a reconnect can target the whole tree.
 */
export const hubHealthKey = ['hub', 'health'] as const;
export const hubChunksKey = ['hub', 'chunks'] as const;
export const hubQueueKey = ['hub', 'queue'] as const;
/** The backlog's (`not_ready` list's) hub-ordered read (`GET /api/backlog`) — ranked
 * independently of the ready queue (`bzh:ranking-is-per-list`), so it gets its own
 * key rather than sharing {@link hubQueueKey}. */
export const hubBacklogKey = ['hub', 'backlog'] as const;
export const hubRunnersKey = ['hub', 'runners'] as const;
export const hubQuestionsKey = ['hub', 'questions'] as const;
/** The operational event feed's key prefix (`GET /api/events`, Phase 4) — a query
 * appends its filter set, so a filter change is its own cache entry and this prefix
 * closes every one of them on an SSE invalidation (TanStack's default prefix match). */
export const hubEventsKey = ['hub', 'events'] as const;
/** The Event log panel's backfill read (`GET /api/activity`, issue #213 Phase 4) — a
 * one-shot read on mount, not re-invalidated by an SSE event: the live tee keeps the
 * feed current after mount, so nothing needs to re-GET this. */
export const hubActivityKey = ['hub', 'activity'] as const;
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
/** The admin page's user listing (issue #94) — `GET /api/users`. Invalidated by the
 * role-assignment mutation directly (no SSE event names a role change yet). */
export const hubUsersKey = ['hub', 'users'] as const;

/** One chunk's full aggregate, keyed by id. */
export function hubChunkKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId];
}

/** One chunk's related work items (issue body + comments), keyed by id. */
export function hubChunkWorkItemsKey(chunkId: string | null): readonly unknown[] {
  return ['hub', 'chunk', chunkId, 'work-items'];
}

/** One minted graph's full structure, keyed by id. */
export function hubGraphKey(graphId: string | null): readonly unknown[] {
  return ['hub', 'graph', graphId];
}

/** Which daemon a transcript-segment query reads from (runner-node-grouped-transcripts,
 * D5) — namespaced so a plane's own live-invalidation event, where one exists, only ever
 * refetches that plane's own cache entries, even though both planes answer the identical
 * wire shape (D2). Only the hub plane has such an event today; see
 * {@link chunkTranscriptsKey} for the runner plane's own gap. */
export type TranscriptPlane = 'hub' | 'runner';

/** One chunk's transcript-segment index (blizzard#248), keyed by plane and id —
 * deliberately under the plane's own chunk-key prefix (`[plane, 'chunk', chunkId]`), so
 * the hub's `chunk-changed` SSE event refetches it: new segments genuinely appear here as
 * the chunk's steps progress. The runner plane carries no equivalent event yet — no
 * `RunnerEventType` names a transcript change (`local-panel/runner-live-updates.ts`'s own
 * registry is exhaustive over the six it does have) — so a runner operator watching a
 * live chunk needs a manual reload to see new segments; this key's placement positions
 * it to pick up a future runner event, it does not itself close today's gap. */
export function chunkTranscriptsKey(plane: TranscriptPlane, chunkId: string | null): readonly unknown[] {
  return [plane, 'chunk', chunkId, 'transcripts'];
}

/** One segment's decompressed turns (blizzard#248), keyed by plane, chunk, and segment
 * id, plus whether the segment is `final` — the placement, not just the id pair, is what
 * decides whether a `chunk-changed` SSE event refetches it (`review:F2`, tightening
 * `review:F6`). A `final` segment's content is immutable and the (chunkId, segmentId)
 * pair already uniquely identifies it, so it gets its own top-level prefix, *not* nested
 * under the plane's chunk-key prefix — nesting it there would mean every SSE event on the
 * chunk refetches an already-rendered segment's content, a decompress+parse+per-turn-
 * validate for no reason, defeating this query's own `refetchInterval: false`
 * (`transcript-segments.query.ts`). A non-`final` (open) segment has no such immutability
 * guarantee — an operator watching it live needs its content to keep refreshing — so it
 * stays under the plane's chunk-key prefix, the same live signal the index itself
 * refetches on. Finality isn't known in advance of the index read, so a caller that
 * hasn't resolved it yet passes `final: false`, the safe (still-live) default. */
export function chunkTranscriptSegmentKey(
  plane: TranscriptPlane,
  chunkId: string | null,
  segmentId: string | null,
  final: boolean,
): readonly unknown[] {
  return final
    ? [plane, 'chunk-transcript-segment', chunkId, segmentId]
    : [plane, 'chunk', chunkId, 'transcript-segment', segmentId];
}

/** The hub-plane transcript-segment index key — a thin, permanently-hub-bound alias of
 * {@link chunkTranscriptsKey} for callers (e.g. the Node History tab) that only ever read
 * the hub's own transcripts and have no reason to thread a plane through. */
export function hubChunkTranscriptsKey(chunkId: string | null): readonly unknown[] {
  return chunkTranscriptsKey('hub', chunkId);
}

/** The hub-plane segment-content key — see {@link hubChunkTranscriptsKey}'s own doc for
 * why a hub-bound alias of {@link chunkTranscriptSegmentKey} stays alongside it. */
export function hubChunkTranscriptSegmentKey(
  chunkId: string | null,
  segmentId: string | null,
  final: boolean,
): readonly unknown[] {
  return chunkTranscriptSegmentKey('hub', chunkId, segmentId, final);
}
