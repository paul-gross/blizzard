import { describe, expect, it } from 'vitest';

import { factText, recordKindLabel, recordLink, recordRoute, credentialLinks } from './config-links.model';

describe('recordKindLabel', () => {
  it('spaces the wire value', () => {
    expect(recordKindLabel('work_source')).toBe('work source');
    expect(recordKindLabel('secret')).toBe('secret');
  });
});

describe('recordRoute', () => {
  it('opens each kind on its own admin surface', () => {
    expect(recordRoute('work_source', 'winter')).toEqual(['/admin', 'work-sources', 'winter']);
    expect(recordRoute('repository', 'blizzard')).toEqual(['/admin', 'repositories', 'blizzard']);
    expect(recordRoute('forge', 'github')).toEqual(['/admin', 'forges', 'github']);
    expect(recordRoute('secret', 'gh')).toEqual(['/admin', 'secrets', 'gh']);
    expect(recordRoute('credential', 'cred_1')).toEqual(['/admin', 'credentials', 'cred_1']);
  });

  it('opens a scope, routine, or cadence on its gardening detail', () => {
    expect(recordRoute('scope', 'blizzard')).toEqual(['/gardening', 'scopes', 'blizzard']);
    expect(recordRoute('routine', 'nightly')).toEqual(['/gardening', 'routines', 'nightly']);
    expect(recordRoute('cadence', 'evening')).toEqual(['/gardening', 'cadences', 'evening']);
  });

  it('answers null for a kind the board has no surface for', () => {
    expect(recordRoute('graph', 'x')).toBeNull();
    expect(recordRoute('project_source_link', 'x')).toBeNull();
    expect(recordRoute('project_repository_link', 'x')).toBeNull();
  });
});

describe('recordLink', () => {
  it('labels and routes a reference', () => {
    expect(recordLink({ kind: 'repository', key: 'blizzard', label: null })).toEqual({
      kind: 'repository',
      name: 'blizzard',
      route: ['/admin', 'repositories', 'blizzard'],
    });
  });
});

describe('recordLink by label', () => {
  it('shows a labelled reference by its label and routes it by its key', () => {
    expect(recordLink({ kind: 'repository', key: 'repo_1', label: 'blizzard' })).toEqual({
      kind: 'repository',
      name: 'blizzard',
      route: ['/admin', 'repositories', 'repo_1'],
    });
  });

  it('shows a work source by its current slug and routes it by its id', () => {
    expect(recordLink({ kind: 'work_source', key: 'wsrc_1', label: 'jira' })).toEqual({
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
