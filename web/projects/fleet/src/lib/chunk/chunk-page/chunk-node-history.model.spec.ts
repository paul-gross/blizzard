import type { ArtifactView } from '../../api/hub';
import { parseSelectedKey, stepArtifacts } from './chunk-node-history.model';

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

describe('parseSelectedKey', () => {
  it('is null when nothing is selected', () => {
    expect(parseSelectedKey(null)).toBeNull();
  });

  it('parses a node-step key into its pair', () => {
    expect(parseSelectedKey('nd_build:2')).toEqual({ nodeId: 'nd_build', epoch: 2 });
  });

  it('is null for a key that is not a node-step key', () => {
    expect(parseSelectedKey('not-a-step')).toBeNull();
  });
});

describe('stepArtifacts', () => {
  const artifacts = [
    artifact({ key: 'late', node_id: 'nd_build', epoch: 1, recorded_at: '2026-01-02T00:00:00Z' }),
    artifact({ key: 'other-node', node_id: 'nd_review', epoch: 1 }),
    artifact({ key: 'other-epoch', node_id: 'nd_build', epoch: 2 }),
    artifact({ key: 'early', node_id: 'nd_build', epoch: 1, recorded_at: '2026-01-01T00:00:00Z' }),
  ];

  it('is empty when no step is selected', () => {
    expect(stepArtifacts(artifacts, null)).toEqual([]);
  });

  it("keeps only the selected step's own artifacts, oldest first", () => {
    expect(stepArtifacts(artifacts, { nodeId: 'nd_build', epoch: 1 }).map((a) => a.key)).toEqual(['early', 'late']);
  });
});
