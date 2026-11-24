import type { ChunkDetailAggregate } from 'fleet';

export type PrerequisiteChunk = Pick<ChunkDetailAggregate, 'chunk_id' | 'status'>;
