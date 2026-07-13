import { injectQuery } from '@tanstack/angular-query-experimental';

import { getChunkApiChunksChunkIdGet, type ChunkDetail } from '../api/hub';

/**
 * Hub `GET /api/chunks/{chunk_id}` read — one chunk's full aggregate: its derived
 * status, current node, **transition history** (every node it visited, including the
 * review that failed once and looped back to build), and its inline **artifact store**
 * (the merged branch pointers and the review-findings notes). This is the surface
 * MVP criterion 9/11 renders (product/mvp.md).
 *
 * Reactive over the selected chunk id: pass an accessor (a signal getter) — the query
 * re-keys and re-fetches as the selection changes, and disables itself (`enabled`)
 * while nothing is selected, so no request fires for the empty board. Real plumbing:
 * the request is the generated openapi-ts SDK call (never hand-written fetch,
 * bzh:generated-client), hitting the daemon the app is served from.
 */
export function injectHubChunkDetailQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: ['hub', 'chunk', id],
      enabled: id !== null,
      queryFn: async (): Promise<ChunkDetail> => {
        const { data, error } = await getChunkApiChunksChunkIdGet({
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: 3000,
    };
  });
}
