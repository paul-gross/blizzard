import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './polling';
import { runnerChunkDetailKey } from './query-keys';

/**
 * Runner `GET /api/chunks/{id}` read — the layered pass-through (panel → its own
 * runner → hub, with the hub's credentials) that carries the full
 * {@link runnerApi.ChunkDetailView} aggregate (issue #314): work-item links,
 * live status, the `pause` fact — the only way the panel learns a chunk is
 * paused — plus transition history and artifacts. Enabled only while a chunk
 * is selected. The `pause` fact is itself hub-sourced, so no runner event
 * proves it directly — but every runner event that names this chunk stales
 * this key too (`runner-live-updates.ts`'s registry, blizzard#317 Phase 4), so
 * the interval below is the backstop that closes the rest (D7), not the
 * primary signal — kept at the same floor as {@link injectRunnerLeasesQuery}
 * so Pause/Resume still self-heals within one operator-visible cadence even
 * with no covering event at all.
 */
export function injectChunkDetailQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: runnerChunkDetailKey(id ?? ''),
      enabled: id !== null,
      queryFn: async (): Promise<runnerApi.ChunkDetailView> => {
        const { data, error } = await runnerApi.getChunkApiChunksChunkIdGet({
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
      refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
