import { injectQuery } from '@tanstack/angular-query-experimental';

import * as hubApi from '../api/hub';
import type { TranscriptSegmentContentView, TranscriptSegmentIndexView } from '../api/hub';
import * as runnerApi from '../api/runner';
import { type DaemonTarget, targetKeyRoot } from '../core/daemon-target/daemon-target';
import { chunkTranscriptSegmentKey, chunkTranscriptsKey } from '../core/query-keys';

/** One chunk's segment index through the target's own generated function
 * (`bzh:generated-client`) — the two specs mirror each other's path shape, but calling
 * through the wrong plane's generated function would make any future divergence between
 * them silent instead of a compile error, so the plane picks which generated module
 * answers, never just which transport `client` the call runs against. */
function fetchSegmentIndex(target: DaemonTarget, chunkId: string) {
  return target.plane === 'hub'
    ? hubApi.listTranscriptSegmentsApiChunksChunkIdTranscriptsGet({
        client: target.client,
        path: { chunk_id: chunkId },
        throwOnError: false,
      })
    : runnerApi.listTranscriptSegmentsRunnersRunnerIdApiChunksChunkIdTranscriptsGet({
        client: target.client,
        path: { runner_id: target.runnerId, chunk_id: chunkId },
        throwOnError: false,
      });
}

/** One segment's turns through the target's own generated function — see {@link fetchSegmentIndex}. */
function fetchSegment(target: DaemonTarget, chunkId: string, segmentId: string) {
  return target.plane === 'hub'
    ? hubApi.getTranscriptSegmentApiChunksChunkIdTranscriptsSegmentIdGet({
        client: target.client,
        path: { chunk_id: chunkId, segment_id: segmentId },
        throwOnError: false,
      })
    : runnerApi.getTranscriptSegmentRunnersRunnerIdApiChunksChunkIdTranscriptsSegmentIdGet({
        client: target.client,
        path: { runner_id: target.runnerId, chunk_id: chunkId, segment_id: segmentId },
        throwOnError: false,
      });
}

/** An error a transcript queryFn throws, carrying the HTTP status the fetch actually
 * returned — the generated `error` value doesn't — so a container can render an
 * honest 403 instead of a generic error. */
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
 * `GET /api/chunks/{chunk_id}/transcripts` read — the chunk's segment index: metadata
 * and byte counts only, never turn
 * content. Target-generic: `target` is the seam a caller crosses to reach either the hub's
 * or a runner's own copy of this identically-shaped route, and its key root namespaces
 * the TanStack cache key ({@link chunkTranscriptsKey}) — a mounting container never
 * branches on which daemon it is; a `null` target holds the read disabled. This query's own
 * `enabled: id !== null` is belt-and-suspenders, not the actual gate: laziness
 * comes from where this query is injected, not from `chunkId`.
 * Permission is gated on `transcript:read` at the backend rather than here, so an
 * identity without it gets a 403 the caller can render as its own state.
 *
 * `target` is an accessor, not a plain value, the same as `chunkId` — not because
 * it is expected to change, but because a caller threading a signal `input.required()`
 * straight through cannot read it eagerly at field-init time
 * (Angular's own `NG8118`: a required input has no value yet at that point in a real
 * template-bound mount); wrapped in a closure, it resolves lazily instead, once
 * `injectQuery`'s own reactive computation actually runs.
 */
export function injectChunkTranscriptsQuery(target: () => DaemonTarget | null, chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    const active = target();
    return {
      queryKey: chunkTranscriptsKey(active === null ? '' : targetKeyRoot(active), id),
      enabled: id !== null && active !== null,
      queryFn: async (): Promise<TranscriptSegmentIndexView> => {
        const { data, error, response } = await fetchSegmentIndex(active!, id!);
        if (error) throw new TranscriptFetchError(response?.status ?? 0);
        return data!;
      },
      retry: shouldRetryTranscriptFetch,
      refetchInterval: false as const,
    };
  });
}

/**
 * `GET /api/chunks/{chunk_id}/transcripts/{segment_id}` read — one segment's turns,
 * fetched lazily: `enabled` only once
 * a segment is actually opened, so listing the index never itself issues a content
 * request. Target-generic, same as {@link injectChunkTranscriptsQuery}. `final` decides
 * whether this query's key stays live to a `chunk-changed` SSE event or is treated as
 * immutable (`chunkTranscriptSegmentKey`) — and is **tri-state**: `null`
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
  target: () => DaemonTarget | null,
  chunkId: () => string | null,
  segmentId: () => string | null,
  final: () => boolean | null,
) {
  return injectQuery(() => {
    const cid = chunkId();
    const sid = segmentId();
    const isFinal = final();
    const active = target();
    return {
      queryKey: chunkTranscriptSegmentKey(active === null ? '' : targetKeyRoot(active), cid, sid, isFinal ?? false),
      enabled: active !== null && cid !== null && sid !== null && isFinal !== null,
      queryFn: async (): Promise<TranscriptSegmentContentView> => {
        const { data, error, response } = await fetchSegment(active!, cid!, sid!);
        if (error) throw new TranscriptFetchError(response?.status ?? 0);
        return data!;
      },
      retry: shouldRetryTranscriptFetch,
      refetchInterval: false as const,
    };
  });
}
