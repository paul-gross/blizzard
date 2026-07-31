import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerChunkDetailKey } from './query-keys';

/**
 * Runner `GET /api/chunks/{id}` read — the layered pass-through (panel → its own
 * runner → hub, with the hub's credentials) that carries the
 * {@link runnerApi.ChunkHeaderView} the chunk-detail dock's header renders off
 * (issue #185): the full chunk id, work-item links, live status, and the
 * `pause` fact — the only way the panel learns a chunk is paused
 * (`ChunkHeaderView`'s own doc comment). Enabled only while a chunk is
 * selected; polls at the same 5s floor as {@link injectRunnerLeasesQuery} so
 * Pause/Resume reflects the server's answer without a manual refresh.
 */
export function injectChunkDetailQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: runnerChunkDetailKey(id ?? ''),
      enabled: id !== null,
      queryFn: async (): Promise<runnerApi.ChunkHeaderView> => {
        const { data, error } = await runnerApi.getChunkApiChunksChunkIdGet({
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: 5000,
    };
  });
}
