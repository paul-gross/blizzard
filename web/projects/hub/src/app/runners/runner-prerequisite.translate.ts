import { laneStatus } from 'fleet/shell';
import type { hubApi } from 'fleet';
import type { PrerequisiteChunk } from './runner-prerequisite.domain';

export function fromPrerequisiteChunk(chunk: hubApi.ChunkDetail): PrerequisiteChunk {
  return { chunk_id: chunk.chunk_id, status: laneStatus(chunk.status) };
}
