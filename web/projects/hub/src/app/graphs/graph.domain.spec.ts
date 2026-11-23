import { describe, expect, it } from 'vitest';

import { graphVocabularyText } from './graph.domain';

describe('graphVocabularyText', () => {
  it('reads a known value as itself', () => {
    expect(graphVocabularyText('hub')).toBe('hub');
  });

  it('reads an unknown value raw', () => {
    expect(graphVocabularyText({ kind: 'unknown', raw: 'cloud' })).toBe('cloud');
  });
});
