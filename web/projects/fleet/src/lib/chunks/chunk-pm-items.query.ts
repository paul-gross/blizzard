import { injectQuery } from '@tanstack/angular-query-experimental';

import { getPmItemsApiChunksChunkIdPmItemsGet, type PmItemsView } from '../api/hub';
import { hubChunkPmItemsKey } from '../query-keys';

/**
 * Hub `GET /api/chunks/{chunk_id}/pm-items` read — the chunk's related PM items
 * : each pointer's issue **body and comment thread**, fetched fresh from
 * the forge and never stored. One entry per pointer, so a grouped chunk surfaces each;
 * a per-pointer forge failure carries an `error` the board renders as a notice rather
 * than failing the whole read. This is the surface the detail dock's Issue tab renders.
 *
 * Reactive over the selected chunk id, exactly like {@link injectHubChunkDetailQuery}:
 * pass an accessor — the query re-keys as the selection changes and disables itself
 * (`enabled`) while nothing is open, so no forge read fires for the empty board. Unlike
 * the chunk aggregate this does **not** poll: the read reaches an external forge (rate
 * limits) and the issue is stable for the life of an open dock, so it fetches
 * once on selection and caches. The request is the generated openapi-ts SDK call
 * (bzh:generated-client), hitting the daemon the app is served from.
 */
export function injectHubChunkPmItemsQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: hubChunkPmItemsKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<PmItemsView> => {
        const { data, error } = await getPmItemsApiChunksChunkIdPmItemsGet({
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
