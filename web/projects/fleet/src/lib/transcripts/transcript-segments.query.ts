import { injectQuery } from '@tanstack/angular-query-experimental';

import * as hubApi from '../api/hub';
import type { TranscriptSegmentContentView, TranscriptSegmentIndexView } from '../api/hub';
import type { Client } from '../api/hub/client';
import { client as hubClient } from '../api/hub/client.gen';
import * as runnerApi from '../api/runner';
import { chunkTranscriptSegmentKey, chunkTranscriptsKey, type TranscriptPlane } from '../query-keys';

/** Each plane's own generated module, keyed by {@link TranscriptPlane} (`bzh:generated-
 * client`) — the two specs mirror each other's path shape (D3), but calling through the
 * wrong plane's generated function would make any future divergence between them silent
 * instead of a compile error, so the plane picks which generated module answers, never
 * just which transport `client` the call runs against. Exported (not part of `fleet`'s
 * public API — absent from `public-api.ts`) purely so this file's own spec can assert the
 * mapping by reference identity, since a frozen ESM namespace export cannot be spied on in
 * place, and this workspace's own Angular test harness refuses `vi.mock` for relative
 * imports outright. */
export const TRANSCRIPT_SEGMENTS_API = { hub: hubApi, runner: runnerApi } as const;

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
 * `GET /api/chunks/{chunk_id}/transcripts` read (blizzard#248 D12, runner-node-grouped-
 * transcripts D5) — the chunk's segment index: metadata and byte counts only, never turn
 * content. Plane-generic: `client` is the seam a caller crosses to reach either the hub's
 * or a runner's own copy of this identically-shaped route (D2/D3), and `plane` only
 * namespaces the TanStack cache key ({@link chunkTranscriptsKey}) — neither this function
 * nor a mounting container branches on which plane it is (D5). This query's own
 * `enabled: id !== null` is belt-and-suspenders, not the actual gate (`review:F5`): a
 * chunk page passes `chunkId` unconditionally, so laziness (D8's "the index on open")
 * comes entirely from where the container that injects this query is mounted — only
 * inside the chunk page's Transcripts-tab branch, never for every chunk selection.
 * Permission is gated on `transcript:read` at the backend rather than here — a deep link
 * held by a viewer-role identity still issues this request once the tab is open, so the
 * 403 renders as the container's own honest state (D9) instead of the tab silently never
 * appearing (which it also doesn't, since the chunk page hides the tab option itself for
 * that identity).
 *
 * `client`/`plane` are accessors, not plain values, the same as `chunkId` — not because
 * either is expected to change, but because a caller threading a signal `input.required()`
 * straight through (`ChunkTranscriptsContainer`) cannot read it eagerly at field-init time
 * (Angular's own `NG8118`: a required input has no value yet at that point in a real
 * template-bound mount); wrapped in a closure, it resolves lazily instead, once
 * `injectQuery`'s own reactive computation actually runs.
 */
export function injectChunkTranscriptsQuery(
  client: () => Client,
  plane: () => TranscriptPlane,
  chunkId: () => string | null,
) {
  return injectQuery(() => {
    const id = chunkId();
    const activePlane = plane();
    return {
      queryKey: chunkTranscriptsKey(activePlane, id),
      enabled: id !== null,
      queryFn: async (): Promise<TranscriptSegmentIndexView> => {
        const { data, error, response } = await TRANSCRIPT_SEGMENTS_API[
          activePlane
        ].listTranscriptSegmentsApiChunksChunkIdTranscriptsGet({
          client: client(),
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

/** The hub-plane transcript index query — a thin, permanently-hub-bound alias of
 * {@link injectChunkTranscriptsQuery} for callers (e.g. the Node History tab) that only
 * ever read the hub's own transcripts and have no reason to thread a client through. */
export function injectHubChunkTranscriptsQuery(chunkId: () => string | null) {
  return injectChunkTranscriptsQuery(
    () => hubClient,
    () => 'hub',
    chunkId,
  );
}

/**
 * `GET /api/chunks/{chunk_id}/transcripts/{segment_id}` read (blizzard#248 D12, runner-
 * node-grouped-transcripts D5) — one segment's turns, fetched lazily: `enabled` only once
 * a segment is actually opened, so listing the index never itself issues a content
 * request. Plane-generic, same as {@link injectChunkTranscriptsQuery}. `final` decides
 * whether this query's key stays live to a `chunk-changed` SSE event or is treated as
 * immutable (`review:F2`, `chunkTranscriptSegmentKey`) — and is **tri-state**: `null`
 * means the index has not named this segment's finality yet, and holds the query disabled
 * rather than fetching against a guessed placement.
 *
 * The gate matters for what it prevents, which is a *second* read: without it a segment id
 * alone enables the query, so the content is fetched once against the guessed `false`
 * placement and again once the index resolves finality to `true` — two decompress+parse+
 * per-turn-validate passes for one segment, the exact cost
 * {@link chunkTranscriptSegmentKey}'s split exists to avoid. Held disabled until
 * finality is known, the key's one transition happens before any fetch has started, so the
 * segment is read exactly once.
 */
export function injectChunkTranscriptSegmentQuery(
  client: () => Client,
  plane: () => TranscriptPlane,
  chunkId: () => string | null,
  segmentId: () => string | null,
  final: () => boolean | null,
) {
  return injectQuery(() => {
    const cid = chunkId();
    const sid = segmentId();
    const isFinal = final();
    const activePlane = plane();
    return {
      queryKey: chunkTranscriptSegmentKey(activePlane, cid, sid, isFinal ?? false),
      enabled: cid !== null && sid !== null && isFinal !== null,
      queryFn: async (): Promise<TranscriptSegmentContentView> => {
        const { data, error, response } = await TRANSCRIPT_SEGMENTS_API[
          activePlane
        ].getTranscriptSegmentApiChunksChunkIdTranscriptsSegmentIdGet({
          client: client(),
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

/** The hub-plane segment-content query — see {@link injectHubChunkTranscriptsQuery}'s own
 * doc for why a hub-bound alias of {@link injectChunkTranscriptSegmentQuery} stays
 * alongside it. */
export function injectHubChunkTranscriptSegmentQuery(
  chunkId: () => string | null,
  segmentId: () => string | null,
  final: () => boolean | null,
) {
  return injectChunkTranscriptSegmentQuery(
    () => hubClient,
    () => 'hub',
    chunkId,
    segmentId,
    final,
  );
}
