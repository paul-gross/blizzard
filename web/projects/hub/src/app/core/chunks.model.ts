import type { ChunkSummary, hubApi } from 'fleet';

export interface BoardChunksRead {
  readonly chunks: ChunkSummary[];
  readonly pages: readonly hubApi.ChunksPageView[];
}
