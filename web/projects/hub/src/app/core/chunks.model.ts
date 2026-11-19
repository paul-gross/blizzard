import type { ChunkSummary, LaneStatus, hubApi } from 'fleet';

export type BoardChunk = ChunkSummary & { readonly laneStatus: LaneStatus };

export interface BoardChunksRead {
  readonly chunks: ChunkSummary[];
  readonly pages: readonly hubApi.ChunksPageView[];
}
