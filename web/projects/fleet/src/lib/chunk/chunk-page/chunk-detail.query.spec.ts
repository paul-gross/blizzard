import { assetArtifactView, chunkDetail, chunkNeighborhoodView, chunkNeighborView } from 'fleet/testing';
import type { ChunkStatus } from '../../api/hub';
import { projectChunkDetail } from './chunk-detail.query';

describe('projectChunkDetail', () => {
  it('projects unknown and absent statuses without changing the cached wire aggregate', () => {
    const wire = chunkDetail({
      status: 'future' as ChunkStatus,
      neighborhood: chunkNeighborhoodView({
        prerequisites: [chunkNeighborView({ status: 'paused' })],
        dependents: [chunkNeighborView({ status: null })],
      }),
    });
    const projected = projectChunkDetail(wire);
    expect(projected.laneStatus).toEqual({ kind: 'unknown', raw: 'future' });
    expect(projected.neighborhood.prerequisites[0].laneStatus).toEqual({ kind: 'paused' });
    expect(projected.neighborhood.dependents[0].laneStatus).toBeNull();
    expect(wire).not.toHaveProperty('laneStatus');
    expect(wire.neighborhood.prerequisites[0]).not.toHaveProperty('laneStatus');
  });

  it('selects domain artifacts alongside the unchanged wire aggregate', () => {
    const artifact = assetArtifactView();
    const wire = chunkDetail({ artifacts: [artifact] });
    const projected = projectChunkDetail(wire);
    expect(projected.artifacts).toBe(wire.artifacts);
    expect(projected.domainArtifacts).toEqual([{ ...artifact, kind: { kind: 'asset' } }]);
    expect(projectChunkDetail(chunkDetail({ artifacts: [] })).domainArtifacts).toEqual([]);
  });
});
