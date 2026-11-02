import { describe, expect, it } from 'vitest';

import { graphStepLabel } from './step-label';

describe('graphStepLabel', () => {
  it('prefixes a node label with its graph once the name is known', () => {
    expect(graphStepLabel('adv-dwf', 'build')).toBe('adv-dwf/build');
    expect(graphStepLabel(null, 'build')).toBe('build');
    expect(graphStepLabel(undefined, 'build')).toBe('build');
    expect(graphStepLabel('', 'build')).toBe('build');
  });

  it('leaves a status word standing in for the node bare', () => {
    expect(graphStepLabel('adv-dwf', 'build', 'stopped')).toBe('stopped');
    expect(graphStepLabel('adv-dwf', 'build', 'build')).toBe('adv-dwf/build');
  });
});
