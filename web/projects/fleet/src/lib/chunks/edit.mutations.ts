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
 * (bzh:generated-client). This collapses onto the same unified PATCH the model edit
 * below uses (issue #124, in #104's shape); `POST /api/chunks/{id}/graph` is now a
 * deprecated alias this board no longer calls. Server-refused 404 for an unknown
 * chunk or target graph, and 409 once the chunk has left `not_ready` (`EditService`)
 * — the chunk detail dock mirrors that refusal so it never offers the edit outside
 * `not_ready`, and surfaces one anyway if the race is lost: a refusal reaches the
 * caller as a thrown error, nothing here swallows it (issue #42's pattern). On
 * success it re-reads the fleet list and the chunk detail; the endpoint's
 * `chunk-changed` SSE frame corroborates for every other open view.
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

/** Repin a not-ready chunk's model selection (issue #27) — the target model name. */
export interface ChunkModelEditVars {
  readonly chunkId: string;
  readonly model: string;
}

/**
 * `PATCH /api/chunks/{id}` with `{ model }` — through the generated client
 * (bzh:generated-client); `POST /api/chunks/{id}/model` is now a deprecated alias
 * this board no longer calls. Server-refused 422 for a blank model, 404 for an
 * unknown chunk, and 409 once the chunk has left `not_ready` (`EditService`) — same
 * refusal-mirroring and report-don't-swallow pattern as
 * {@link injectSetChunkGraphMutation}, and the same on-success invalidation.
 */
export function injectSetChunkModelMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ChunkModelEditVars): Promise<void> => {
      const { error } = await patchChunkApiChunksChunkIdPatch({
        path: { chunk_id: vars.chunkId },
        body: { model: vars.model },
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
