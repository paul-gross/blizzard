import { injectQuery } from '@tanstack/angular-query-experimental';

import * as hubApi from '../../api/hub';
import type { ChunkDetail, WorkItemsView } from '../../api/hub';
import type { Client } from '../../api/hub/client';
import { client as hubClient } from '../../api/hub/client.gen';
import * as runnerApi from '../../api/runner';
import { LIVE_COVERED_POLL_BACKSTOP_MS, RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from '../../core/polling';
import { chunkDetailKey, chunkWorkItemsKey, type TranscriptPlane } from '../../core/query-keys';

/** Each plane's own generated module, keyed by {@link TranscriptPlane} — the same
 * plane-picks-the-module stance as `transcripts/transcript-segments.query.ts`'s
 * `TRANSCRIPT_SEGMENTS_API` (`bzh:generated-client`): the two specs declare the
 * chunk-detail and work-items DTOs field for field, but calling through the wrong plane's
 * generated function would make a future divergence silent rather than a compile error. */
const CHUNK_DETAIL_API = { hub: hubApi, runner: runnerApi } as const;

/** Each plane's live-covered backstop — the interval its own live-update registry
 * leaves as insurance against a dropped frame. */
const POLL_BACKSTOP_MS: Record<TranscriptPlane, number> = {
  hub: LIVE_COVERED_POLL_BACKSTOP_MS,
  runner: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
};

/**
 * `GET /api/chunks/{chunk_id}` read — one chunk's full aggregate: its derived status,
 * current node, transition history, inline artifact store, and the `pause` fact. The hub
 * serves it from its own store; a runner serves the identically-shaped route as a
 * pass-through to the hub, with the hub's credentials.
 *
 * Plane-generic: `client` is the seam a caller crosses to reach either daemon, and
 * `plane` picks the generated module, the cache key ({@link chunkDetailKey}, under the
 * prefix that plane's live-update registry invalidates), and the backstop interval.
 * Reactive over the chunk id — disabled while it is `null`. `client`/`plane` are
 * accessors for the same reason `injectChunkTranscriptsQuery`'s are.
 */
export function injectChunkDetailQuery(
  client: () => Client,
  plane: () => TranscriptPlane,
  chunkId: () => string | null,
) {
  return injectQuery(() => {
    const id = chunkId();
    const activePlane = plane();
    return {
      queryKey: chunkDetailKey(activePlane, id),
      enabled: id !== null,
      queryFn: async (): Promise<ChunkDetail> => {
        const { data, error } = await CHUNK_DETAIL_API[activePlane].getChunkApiChunksChunkIdGet({
          client: client(),
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      // Hub: covered by chunk-changed, question-asked/-answered, decision-opened/-resolved,
      // and event-logged when named (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts).
      // Runner: staled by every runner event naming the chunk. See polling.ts.
      refetchInterval: POLL_BACKSTOP_MS[activePlane],
    };
  });
}

/**
 * `GET /api/chunks/{chunk_id}/work-items` read — the chunk's related work items: each
 * pointer's issue body and comment thread, fetched fresh from the forge and never
 * stored. A per-pointer forge failure carries an `error` rendered as a notice rather
 * than failing the whole read.
 *
 * Plane-generic, the same seam as {@link injectChunkDetailQuery}. Unlike the aggregate
 * this does **not** poll: the read reaches an external forge (rate limits) and the issue
 * is stable for the life of an open view, so it fetches once and caches.
 */
export function injectChunkWorkItemsQuery(
  client: () => Client,
  plane: () => TranscriptPlane,
  chunkId: () => string | null,
) {
  return injectQuery(() => {
    const id = chunkId();
    const activePlane = plane();
    return {
      queryKey: chunkWorkItemsKey(activePlane, id),
      enabled: id !== null,
      queryFn: async (): Promise<WorkItemsView> => {
        const { data, error } = await CHUNK_DETAIL_API[activePlane].getWorkItemsApiChunksChunkIdWorkItemsGet({
          client: client(),
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    };
  });
}

/** The hub-plane aggregate read — a thin, permanently-hub-bound alias of
 * {@link injectChunkDetailQuery} for the hub's own callers (the board dock, the
 * single-artifact page) that have no reason to thread a client through. */
export function injectHubChunkDetailQuery(chunkId: () => string | null) {
  return injectChunkDetailQuery(
    () => hubClient,
    () => 'hub',
    chunkId,
  );
}

/** The hub-plane work-items read — see {@link injectHubChunkDetailQuery}. */
export function injectHubChunkWorkItemsQuery(chunkId: () => string | null) {
  return injectChunkWorkItemsQuery(
    () => hubClient,
    () => 'hub',
    chunkId,
  );
}
