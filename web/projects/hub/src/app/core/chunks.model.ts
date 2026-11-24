import type { ChunkSummary, LaneStatus, hubApi } from 'fleet';
import type { GraphProgressNode } from './chunks.domain';

export type BoardChunk = ChunkSummary & {
  readonly laneStatus: LaneStatus;
  readonly graphProgress: readonly GraphProgressNode[];
};

export interface BoardChunksRead {
  readonly chunks: ChunkSummary[];
  readonly pages: readonly hubApi.ChunksPageView[];
}
