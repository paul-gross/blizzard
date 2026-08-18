import type { ArtifactView } from '../api/hub';
import { filterArtifactsByStep } from './filter-artifacts-by-step';

function artifact(overrides: Partial<ArtifactView>): ArtifactView {
  return {
    key: 'k',
    kind: 'asset',
    name: 'n',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    ...overrides,
  };
}

describe('filterArtifactsByStep', () => {
  it('keeps only the entries matching the exact (node_id, epoch) pair', () => {
    const artifacts = [
      artifact({ key: 'a', node_id: 'nd_build', epoch: 1 }),
      artifact({ key: 'b', node_id: 'nd_review', epoch: 1 }),
      artifact({ key: 'c', node_id: 'nd_build', epoch: 2 }),
    ];
    expect(filterArtifactsByStep(artifacts, 'nd_build', 1).map((a) => a.key)).toEqual(['a']);
  });

  it('does not fall back to the latest epoch for a node re-run under a later one', () => {
    // A re-entered node under a later epoch must not surface the earlier epoch's
    // artifacts — exact equality, never latest-by-node.
    const artifacts = [
      artifact({ key: 'old', node_id: 'nd_build', epoch: 1 }),
      artifact({ key: 'new', node_id: 'nd_build', epoch: 3 }),
    ];
    expect(filterArtifactsByStep(artifacts, 'nd_build', 2).map((a) => a.key)).toEqual([]);
  });

  it('returns an empty array for a step with no artifacts', () => {
    expect(filterArtifactsByStep([], 'nd_build', 1)).toEqual([]);
  });
});
