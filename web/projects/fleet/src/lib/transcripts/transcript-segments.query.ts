import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getTranscriptSegmentApiChunksChunkIdTranscriptsSegmentIdGet,
  listTranscriptSegmentsApiChunksChunkIdTranscriptsGet,
  type TranscriptSegmentContentView,
  type TranscriptSegmentIndexView,
} from '../api/hub';
import { hubChunkTranscriptSegmentKey, hubChunkTranscriptsKey } from '../query-keys';

/** An error a transcript queryFn throws, carrying the HTTP status the fetch actually
 * returned — the generated `error` value doesn't — so a container can render an
 * honest 403 (blizzard#248 D9) instead of a generic error. */
export class TranscriptFetchError extends Error {
  constructor(readonly status: number) {
    super(`transcript fetch failed with status ${status}`);
  }
}

/** Neither a 403 (no `transcript:read`) nor a 404 (unknown chunk/segment) is
 * transient — both are terminal answers a retry cannot change. */
export function shouldRetryTranscriptFetch(failureCount: number, error: Error): boolean {
  const terminal = error instanceof TranscriptFetchError && (error.status === 403 || error.status === 404);
  return !terminal && failureCount < 3;
}

/**
 * Hub `GET /api/chunks/{chunk_id}/transcripts` read (blizzard#248 D12) — the chunk's
 * segment index: metadata and byte counts only, never turn content. This query's own
 * `enabled: id !== null` is belt-and-suspenders, not the actual gate (`review:F5`): the
 * hub's chunk page passes `chunkId` unconditionally, so laziness (D8's "the index on
 * open") comes entirely from where the container that injects this query is mounted —
 * only inside the chunk page's Transcripts-tab branch, never for every chunk selection.
 * Permission is gated on `transcript:read` at the backend rather than here — a deep link
 * held by a viewer-role identity still issues this request once the tab is open, so the
 * 403 renders as the container's own honest state (D9) instead of the tab silently never
 * appearing (which it also doesn't, since the chunk page hides the tab option itself for
 * that identity).
 */
export function injectHubChunkTranscriptsQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: hubChunkTranscriptsKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<TranscriptSegmentIndexView> => {
        const { data, error, response } = await listTranscriptSegmentsApiChunksChunkIdTranscriptsGet({
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw new TranscriptFetchError(response?.status ?? 0);
        return data!;
      },
      retry: shouldRetryTranscriptFetch,
      refetchInterval: false as const,
    };
  });
}

/**
 * Hub `GET /api/chunks/{chunk_id}/transcripts/{segment_id}` read (blizzard#248 D12) —
 * one segment's turns, fetched lazily: `enabled` only once a segment is actually
 * opened, so listing the index never itself issues a content request.
 */
export function injectHubChunkTranscriptSegmentQuery(chunkId: () => string | null, segmentId: () => string | null) {
  return injectQuery(() => {
    const cid = chunkId();
    const sid = segmentId();
    return {
      queryKey: hubChunkTranscriptSegmentKey(cid, sid),
      enabled: cid !== null && sid !== null,
      queryFn: async (): Promise<TranscriptSegmentContentView> => {
        const { data, error, response } = await getTranscriptSegmentApiChunksChunkIdTranscriptsSegmentIdGet({
          path: { chunk_id: cid!, segment_id: sid! },
          throwOnError: false,
        });
        if (error) throw new TranscriptFetchError(response?.status ?? 0);
        return data!;
      },
      retry: shouldRetryTranscriptFetch,
      refetchInterval: false as const,
    };
  });
}
