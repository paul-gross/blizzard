import { RecordKind } from 'fleet';
import { describe, expect, it } from 'vitest';

import { factText, recordKindLabel, recordLink, recordRoute, secretLinks } from './config-links.model';

describe('recordKindLabel', () => {
  it('spaces the wire value', () => {
    expect(recordKindLabel(RecordKind.WORK_SOURCE)).toBe('work source');
    expect(recordKindLabel(RecordKind.SECRET)).toBe('secret');
  });
});

describe('recordRoute', () => {
  it('opens each kind on its own admin surface', () => {
    expect(recordRoute(RecordKind.WORK_SOURCE, 'winter')).toEqual(['/admin', 'work-sources', 'winter']);
    expect(recordRoute(RecordKind.REPOSITORY, 'blizzard')).toEqual(['/admin', 'repositories', 'blizzard']);
    expect(recordRoute(RecordKind.SECRET, 'gh')).toEqual(['/admin', 'secrets', 'gh']);
  });

  it('answers null for a kind the board has no surface for', () => {
    expect(recordRoute('routine', 'x')).toBeNull();
  });
});

describe('recordLink', () => {
  it('labels and routes a reference', () => {
    expect(recordLink({ kind: RecordKind.REPOSITORY, key: 'blizzard' })).toEqual({
      kind: 'repository',
      name: 'blizzard',
      route: ['/admin', 'repositories', 'blizzard'],
    });
  });
});

describe('secretLinks', () => {
  it('links a named secret, and nothing for none', () => {
    expect(secretLinks('gh')).toEqual([{ kind: 'secret', name: 'gh', route: ['/admin', 'secrets', 'gh'] }]);
    expect(secretLinks(null)).toEqual([]);
    expect(secretLinks('')).toEqual([]);
  });
});

describe('factText', () => {
  it('renders an unset value as a dash', () => {
    expect(factText(null)).toBe('—');
    expect(factText(undefined)).toBe('—');
    expect(factText('')).toBe('—');
    expect(factText('github')).toBe('github');
  });
});
