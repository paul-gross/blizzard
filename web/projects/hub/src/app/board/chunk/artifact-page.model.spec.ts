import { type ArtifactView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { artifactByKey } from './artifact-page.model';

const artifact = (key: string): ArtifactView => ({
  epoch: 0,
  key,
  kind: 'asset',
  name: key,
  node_id: 'nd_build',
  node_name: 'build',
});

describe('artifactByKey', () => {
  const artifacts = [artifact('plan-0'), artifact('review-0')];

  it('finds the artifact the key names', () => {
    expect(artifactByKey(artifacts, 'review-0')).toBe(artifacts[1]);
  });

  it('is undefined without a key', () => {
    expect(artifactByKey(artifacts, null)).toBeUndefined();
  });

  it('is undefined when the key names nothing', () => {
    expect(artifactByKey(artifacts, 'missing')).toBeUndefined();
  });
});
