import type { ChunkSummary, hubApi } from 'fleet';
import type { BoardSummary } from './chunks.domain';

export type BoardChunk = BoardSummary;

export interface BoardChunksRead {
  readonly chunks: ChunkSummary[];
  readonly pages: readonly hubApi.ChunksPageView[];
}
