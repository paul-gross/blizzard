import type { ChunkArtifactStore } from '../../chunk/chunk-detail/artifact.model';

/** A chunk's artifact store for presentational components. */
export function chunkArtifactStore(overrides: Partial<ChunkArtifactStore> = {}): ChunkArtifactStore {
  return { chunk_id: 'ch_fixture', artifacts: [], ...overrides };
}
