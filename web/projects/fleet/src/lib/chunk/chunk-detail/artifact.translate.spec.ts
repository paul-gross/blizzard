import { assetArtifactView, commitArtifactView } from 'fleet/testing';
import type { ArtifactView } from '../../api/hub';
import { fromArtifactView } from './artifact.translate';

describe('fromArtifactView', () => {
  it('copies asset and commit fields without changing the cached wire object', () => {
    for (const wire of [assetArtifactView(), commitArtifactView()]) {
      expect(fromArtifactView(wire)).toEqual({ ...wire, kind: { kind: wire.kind } });
      expect(typeof wire.kind).toBe('string');
    }
  });

  it('retains an unknown kind and partial artifact fields', () => {
    const wire = assetArtifactView({ kind: 'future' as ArtifactView['kind'], content: null, recorded_at: null });
    expect(fromArtifactView(wire)).toEqual({ ...wire, kind: { kind: 'unknown', raw: 'future' } });
  });
});
