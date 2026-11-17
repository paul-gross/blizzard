/**
 * The plane-root query-key helpers both daemons' chunk pages read through. A key leads with its plane's root — `'hub'` or
 * a runner id — and each app owns the rest of its own keys (`hub/core/query-keys.ts`, `runner/core/query-keys.ts`).
 */

/** Which generated module a plane-generic read calls through — the hub's or a runner's.
 * Picks the module only; a read's cache key leads with the target's own key root
 * (`'hub'` or a runner id, `daemon-target.ts`'s `targetKeyRoot`), so one runner's live
 * events refetch only that runner's entries. */
export type TranscriptPlane = 'hub' | 'runner';

/** One chunk's full aggregate under a key root — `'hub'` or a runner id. Each sits under
 * the prefix its own live-update registry invalidates: the hub's `['hub', 'chunk', id]`, and
 * a runner's `[runnerId, chunk, id, detail]`. */
export function chunkDetailKey(root: string, chunkId: string | null): readonly unknown[] {
  return root === 'hub' ? ['hub', 'chunk', chunkId] : [root, 'chunk', chunkId, 'detail'];
}

/** One chunk's related work items under a key root. A runner's entry is its own
 * `work-items-detail` key so it never shares a cache entry or observer options with
 * another read of the same endpoint. */
export function chunkWorkItemsKey(root: string, chunkId: string | null): readonly unknown[] {
  return root === 'hub' ? ['hub', 'chunk', chunkId, 'work-items'] : [root, 'chunk', chunkId, 'work-items-detail'];
}

/** One chunk's transcript-segment index under a key root —
 * deliberately under that root's chunk-key prefix (`[root, 'chunk', chunkId]`), so
 * the hub's `chunk-changed` SSE event refetches it: new segments genuinely appear here as
 * the chunk's steps progress. A runner carries no equivalent event — no
 * `RunnerEventType` names a transcript change — so a runner's read sees new
 * segments only on reload. */
export function chunkTranscriptsKey(root: string, chunkId: string | null): readonly unknown[] {
  return [root, 'chunk', chunkId, 'transcripts'];
}

/** One segment's decompressed turns, keyed by root, chunk, and segment id, plus
 * whether the segment is `final`. A `final` segment sits under its own top-level
 * prefix and an open one under the root's chunk-key prefix, so only the open one
 * refetches on a `chunk-changed` event; pinned by `transcript-segments.query.spec.ts`'s
 * "refetches an open segment on the chunk key's invalidation, but never a final one".
 * A caller that has not resolved finality yet passes `final: false`. */
export function chunkTranscriptSegmentKey(
  root: string,
  chunkId: string | null,
  segmentId: string | null,
  final: boolean,
): readonly unknown[] {
  return final
    ? [root, 'chunk-transcript-segment', chunkId, segmentId]
    : [root, 'chunk', chunkId, 'transcript-segment', segmentId];
}
