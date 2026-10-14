import { injectQuery } from '@tanstack/angular-query-experimental';

import * as hubApi from '../../api/hub';
import type { ChunkDetail, WorkItemsView } from '../../api/hub';
import { client as hubClient } from '../../api/hub/client.gen';
import * as runnerApi from '../../api/runner';
import { LIVE_COVERED_POLL_BACKSTOP_MS, RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from '../../core/polling';
import { type DaemonTarget, targetKeyRoot } from '../../core/daemon-target/daemon-target';
import { chunkDetailKey, chunkWorkItemsKey, type TranscriptPlane } from '../../core/query-keys';

/** Each plane's live-covered backstop — the interval its own live-update registry
 * leaves as insurance against a dropped frame. */
const POLL_BACKSTOP_MS: Record<TranscriptPlane, number> = {
  hub: LIVE_COVERED_POLL_BACKSTOP_MS,
  runner: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
};

/** One chunk's aggregate through the target's own generated function — each plane calls
 * its own module (`bzh:generated-client`): the two specs declare the chunk-detail DTO
 * field for field, but calling through the wrong plane's function would make a future
 * divergence silent rather than a compile error. */
async function fetchChunkDetail(target: DaemonTarget, chunkId: string): Promise<ChunkDetail> {
  const { data, error } =
    target.plane === 'hub'
      ? await hubApi.getChunkApiChunksChunkIdGet({ client: target.client, path: { chunk_id: chunkId }, throwOnError: false })
      : await runnerApi.getChunkRunnersRunnerIdApiChunksChunkIdGet({
          client: target.client,
          path: { runner_id: target.runnerId, chunk_id: chunkId },
          throwOnError: false,
        });
  if (error) throw error;
  return data!;
}

/** One chunk's work items through the target's own generated function — see {@link fetchChunkDetail}. */
async function fetchChunkWorkItems(target: DaemonTarget, chunkId: string): Promise<WorkItemsView> {
  const { data, error } =
    target.plane === 'hub'
      ? await hubApi.getWorkItemsApiChunksChunkIdWorkItemsGet({
          client: target.client,
          path: { chunk_id: chunkId },
          throwOnError: false,
        })
      : await runnerApi.getWorkItemsRunnersRunnerIdApiChunksChunkIdWorkItemsGet({
          client: target.client,
          path: { runner_id: target.runnerId, chunk_id: chunkId },
          throwOnError: false,
        });
  if (error) throw error;
  return data!;
}

/**
 * `GET /api/chunks/{chunk_id}` read — one chunk's full aggregate: its derived status,
 * current node, transition history, inline artifact store, and the `pause` fact. The hub
 * serves it from its own store; a runner serves the identically-shaped route under
 * `/runners/{runner_id}` as a pass-through to the hub, with the hub's credentials.
 *
 * Target-generic: `target` is the daemon the read crosses to — it picks the generated
 * module, the cache key ({@link chunkDetailKey}, under the key root that daemon's
 * live-update registry invalidates), and the backstop interval. Reactive over the target
 * and the chunk id — disabled while either is `null`. Both are accessors for the same
 * reason `injectChunkTranscriptsQuery`'s are.
 */
export function injectChunkDetailQuery(target: () => DaemonTarget | null, chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    const active = target();
    return {
      queryKey: chunkDetailKey(active === null ? '' : targetKeyRoot(active), id),
      enabled: id !== null && active !== null,
      queryFn: () => fetchChunkDetail(active!, id!),
      // Hub: covered by chunk-changed, question-asked/-answered, decision-opened/-resolved,
      // and event-logged when named (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts).
      // Runner: staled by every runner event naming the chunk. See polling.ts.
      refetchInterval: POLL_BACKSTOP_MS[active?.plane ?? 'hub'],
    };
  });
}

/**
 * `GET /api/chunks/{chunk_id}/work-items` read — the chunk's related work items: each
 * pointer's issue body and comment thread, fetched fresh from the forge and never
 * stored. A per-pointer forge failure carries an `error` rendered as a notice rather
 * than failing the whole read.
 *
 * Target-generic, the same seam as {@link injectChunkDetailQuery}. Unlike the aggregate
 * this does **not** poll: the read reaches an external forge (rate limits) and the issue
 * is stable for the life of an open view, so it fetches once and caches.
 */
export function injectChunkWorkItemsQuery(target: () => DaemonTarget | null, chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    const active = target();
    return {
      queryKey: chunkWorkItemsKey(active === null ? '' : targetKeyRoot(active), id),
      enabled: id !== null && active !== null,
      queryFn: () => fetchChunkWorkItems(active!, id!),
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    };
  });
}

/** The hub's own target — the hub client, under the `'hub'` key root. */
const HUB_TARGET: DaemonTarget = { plane: 'hub', client: hubClient };

/** The hub-plane aggregate read — a thin, permanently-hub-bound alias of
 * {@link injectChunkDetailQuery} for the hub's own callers (the board dock, the
 * single-artifact page) that have no reason to thread a target through. */
export function injectHubChunkDetailQuery(chunkId: () => string | null) {
  return injectChunkDetailQuery(() => HUB_TARGET, chunkId);
}

/** The hub-plane work-items read — see {@link injectHubChunkDetailQuery}. */
export function injectHubChunkWorkItemsQuery(chunkId: () => string | null) {
  return injectChunkWorkItemsQuery(() => HUB_TARGET, chunkId);
}
