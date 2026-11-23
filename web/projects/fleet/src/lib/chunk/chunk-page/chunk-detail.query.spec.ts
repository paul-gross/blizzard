import { assetArtifactView, chunkDetail, chunkNeighborhoodView, chunkNeighborView, transitionView } from 'fleet/testing';
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
  it('projects only transcript history and current-step facts, leaving legacy fields intact', () => {
    const history = [transitionView({ from_node_id: 'build', from_node_name: 'Build', epoch: 7 })];
    const wire = chunkDetail({ history, current_node_id: 'review', current_node_name: null, latest_epoch: 8, current_node_terminal: false });
    const projected = projectChunkDetail(wire);
    expect(projected.history).toBe(history);
    expect(projected.transcriptContext).toEqual({
      history: [{ from_node_id: 'build', from_node_name: 'Build', epoch: 7 }],
      current: { nodeId: 'review', nodeName: null, epoch: 8, terminal: false },
    });
    expect(projected.transcriptContext.history[0]).not.toBe(history[0]);
    expect(projectChunkDetail(chunkDetail({ history: [] })).transcriptContext.history).toEqual([]);
  });

});
