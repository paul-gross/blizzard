import type { RepositorySummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import { repositoryBadges, repositoryCreate, repositoryRecordVm, repositoryRows } from './repositories.model';

const BLIZZARD: RepositorySummary = {
  repository_id: 'repo_1',
  slug: 'blizzard',
  name: 'blizzard',
  owner: 'paul-gross',
  repo: 'blizzard',
  base_branch: 'master',
  forge_api_url: 'https://api.github.com',
  secret_name: 'gh-blizzard',
  revision: 2,
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'pgross',
};
const OLD: RepositorySummary = { ...BLIZZARD, repository_id: 'repo_2', slug: 'old', name: 'Old one', retired: true };

describe('repositoryBadges', () => {
  it('badges only a retired repository', () => {
    expect(repositoryBadges(OLD).map((b) => b.label)).toEqual(['retired']);
    expect(repositoryBadges(BLIZZARD)).toEqual([]);
  });
});

describe('repositoryRows', () => {
  it('filters by lifecycle and maps each repository', () => {
    expect(repositoryRows([BLIZZARD, OLD], 'all').map((r) => r.key)).toEqual(['repo_1', 'repo_2']);
    expect(repositoryRows([BLIZZARD, OLD], 'active')).toEqual([
      {
        key: 'repo_1',
        title: 'blizzard',
        sub: ['paul-gross/blizzard', 'base master'],
        badges: [],
        revision: 2,
        retired: false,
      },
    ]);
  });
});

describe('repositoryRecordVm', () => {
  it('answers null with none loaded', () => {
    expect(repositoryRecordVm(undefined)).toBeNull();
  });

  it('shows the fields, revision, and token link', () => {
    const vm = repositoryRecordVm(BLIZZARD)!;
    expect(vm.facts.map((f) => [f.label, f.value])).toEqual([
      ['Name', 'blizzard'],
      ['Forge API', 'https://api.github.com'],
      ['Owner', 'paul-gross'],
      ['Repository', 'blizzard'],
      ['Base branch', 'master'],
      ['Created by', 'pgross'],
    ]);
    expect(vm.revision).toBe(2);
    expect(vm.links?.links.map((l) => l.name)).toEqual(['gh-blizzard']);
    expect(vm.hasHistory).toBe(true);
  });

  it('shows who created it by name, falling back to the id', () => {
    const createdBy = (repository: RepositorySummary) => repositoryRecordVm(repository)!.facts.find((f) => f.label === 'Created by')?.value;
    expect(createdBy({ ...BLIZZARD, created_by_name: 'Paul Gross' })).toBe('Paul Gross');
    expect(createdBy(BLIZZARD)).toBe('pgross');
  });

  it('titles a row by slug and shows a name that differs from it', () => {
    expect(repositoryRows([OLD], 'all')[0]).toMatchObject({ title: 'old', sub: ['Old one', 'paul-gross/blizzard', 'base master'] });
  });

  it('notes a retired repository', () => {
    expect(repositoryRecordVm(OLD)?.note).toContain('Retired');
  });

  it('creates a repository from the form, leaving an empty name out', () => {
    const body = {
      slug: 'blizzard',
      name: '',
      forge_api_url: 'https://api.github.com',
      owner: 'paul-gross',
      repo: 'blizzard',
      base_branch: 'master',
      secret_name: 'gh-blizzard',
    };
    expect(repositoryCreate(body)).toEqual({
      slug: 'blizzard',
      forge_api_url: 'https://api.github.com',
      owner: 'paul-gross',
      repo: 'blizzard',
      base_branch: 'master',
      secret_name: 'gh-blizzard',
    });
    expect(repositoryCreate({ ...body, name: 'Blizzard' }).name).toBe('Blizzard');
  });
});
