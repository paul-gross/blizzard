import { RecordKind } from 'fleet';
import { describe, expect, it } from 'vitest';

import { factText, recordKindLabel, recordLink, recordRoute, credentialLinks } from './config-links.model';

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
    expect(recordRoute(RecordKind.FORGE, 'github')).toEqual(['/admin', 'forges', 'github']);
    expect(recordRoute(RecordKind.SECRET, 'gh')).toEqual(['/admin', 'secrets', 'gh']);
    expect(recordRoute(RecordKind.CREDENTIAL, 'cred_1')).toEqual(['/admin', 'credentials', 'cred_1']);
  });

  it('opens a scope, routine, or cadence on its gardening detail', () => {
    expect(recordRoute(RecordKind.SCOPE, 'blizzard')).toEqual(['/gardening', 'scopes', 'blizzard']);
    expect(recordRoute(RecordKind.ROUTINE, 'nightly')).toEqual(['/gardening', 'routines', 'nightly']);
    expect(recordRoute(RecordKind.CADENCE, 'evening')).toEqual(['/gardening', 'cadences', 'evening']);
  });

  it('answers null for a kind the board has no surface for', () => {
    expect(recordRoute('graph', 'x')).toBeNull();
    expect(recordRoute(RecordKind.PROJECT_SOURCE_LINK, 'x')).toBeNull();
    expect(recordRoute(RecordKind.PROJECT_REPOSITORY_LINK, 'x')).toBeNull();
  });
});

describe('recordLink', () => {
  it('labels and routes a reference', () => {
    expect(recordLink({ kind: RecordKind.REPOSITORY, key: 'blizzard', label: null })).toEqual({
      kind: 'repository',
      name: 'blizzard',
      route: ['/admin', 'repositories', 'blizzard'],
    });
  });
});

describe('recordLink by label', () => {
  it('shows a labelled reference by its label and routes it by its key', () => {
    expect(recordLink({ kind: RecordKind.REPOSITORY, key: 'repo_1', label: 'blizzard' })).toEqual({
      kind: 'repository',
      name: 'blizzard',
      route: ['/admin', 'repositories', 'repo_1'],
    });
  });

  it('shows a work source by its current slug and routes it by its id', () => {
    expect(recordLink({ kind: RecordKind.WORK_SOURCE, key: 'wsrc_1', label: 'jira' })).toEqual({
      kind: 'work source',
      name: 'jira',
      route: ['/admin', 'work-sources', 'wsrc_1'],
    });
  });
});

describe('credentialLinks', () => {
  it('links a named credential by its slug, and nothing for none', () => {
    expect(credentialLinks('cred_1', 'gh-main')).toEqual([{ kind: 'credential', name: 'gh-main', route: ['/admin', 'credentials', 'cred_1'] }]);
    expect(credentialLinks(null, null)).toEqual([]);
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
