import type { RepositorySummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import { repositoryBadges, repositoryRecordVm, repositoryRows } from './repositories.model';

const BLIZZARD: RepositorySummary = {
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
const OLD: RepositorySummary = { ...BLIZZARD, name: 'old', retired: true };

describe('repositoryBadges', () => {
  it('badges only a retired repository', () => {
    expect(repositoryBadges(OLD).map((b) => b.label)).toEqual(['retired']);
    expect(repositoryBadges(BLIZZARD)).toEqual([]);
  });
});

describe('repositoryRows', () => {
  it('filters by lifecycle and maps each repository', () => {
    expect(repositoryRows([BLIZZARD, OLD], 'all').map((r) => r.key)).toEqual(['blizzard', 'old']);
    expect(repositoryRows([BLIZZARD, OLD], 'active')).toEqual([
      {
        key: 'blizzard',
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

  it('notes a retired repository', () => {
    expect(repositoryRecordVm(OLD)?.note).toContain('Retired');
  });
});
