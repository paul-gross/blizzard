import { describe, expect, it } from 'vitest';

import { EMPTY_CADENCE_DRAFT } from './cadence.domain';
import { vocabularyText } from './vocabulary.domain';

describe('vocabularyText', () => {
  it('reads a known value as itself and an unlisted one raw', () => {
    expect(vocabularyText<'full'>('full')).toBe('full');
    expect(vocabularyText<'full'>({ unlisted: 'sampled' })).toBe('sampled');
  });
});

describe('EMPTY_CADENCE_DRAFT', () => {
  it('states nothing: every field blank, the anchor unstated', () => {
    expect(EMPTY_CADENCE_DRAFT).toEqual({ slug: '', name: '', cron: '', zone: '', spacing: '', anchor: 'unstated' });
  });
});
