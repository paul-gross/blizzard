import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { patchChunkApiChunksChunkIdPatch } from '../api/hub';
import { hubChunkKey, hubChunksKey } from '../query-keys';

/** Repin a not-ready chunk's workflow graph (issue #27) — the target graph's id. */
export interface ChunkGraphEditVars {
  readonly chunkId: string;
  readonly graphId: string;
}

/**
 * `PATCH /api/chunks/{id}` with `{ graph_id }` — through the generated client
 * (bzh:generated-client) — the unified all-or-nothing PATCH (issue #124, in #104's
 * shape). Server-refused 404 for an unknown
 * chunk or target graph, and 409 once the chunk has left `not_ready` (`EditService`)
 * — the chunk detail dock mirrors that refusal so it never offers the edit outside
 * `not_ready`, and surfaces one anyway if the race is lost: a refusal reaches the
 * caller as a thrown error, nothing here swallows it (issue #42's pattern). On
 * success it re-reads the fleet list and the chunk detail; the endpoint's
 * `chunk-changed` SSE frame corroborates for every other open view.
 *
 * The chunk's model selection was editable here too until issue #144 retired
 * `Chunk.model` for the `default_model`/`default_effort` pair, which has no web editing
 * surface — `blizzard hub chunk set --default-model/--default-effort` is the one way to
 * write them, and `chunk show` reads them back.
 */
export function injectSetChunkGraphMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ChunkGraphEditVars): Promise<void> => {
      const { error } = await patchChunkApiChunksChunkIdPatch({
        path: { chunk_id: vars.chunkId },
        body: { graph_id: vars.graphId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubChunksKey });
      void queryClient.invalidateQueries({ queryKey: hubChunkKey(vars.chunkId) });
    },
  }));
}
