import type { RepositorySummary } from 'fleet';
import { describe, expect, it } from 'vitest';

import { createBody, missingRequired, shownFields } from '../config-edit.model';
import {
  chosenForgeOffered,
  LANDING_POLICIES_KEY,
  landingPolicies,
  landingPolicyOptions,
  REPOSITORY_FIELDS,
  repositoryBadges,
  repositoryCreate,
  repositoryFields,
  repositoryRecordVm,
  repositoryRows,
} from './repositories.model';

const BLIZZARD: RepositorySummary = {
  forge_id: 'frg_github',
  forge_slug: 'github',
  forge_name: 'GitHub',
  warnings: [],
  repository_id: 'repo_1',
  slug: 'blizzard',
  name: 'blizzard',
  owner: 'paul-gross',
  repo: 'blizzard',
  base_branch: 'master',
  forge_api_url: 'https://api.github.com',
  secret_name: 'gh-blizzard',
  landing_policy: 'merge-on-checks',
  park_timeout_hours: null,
  revision: 2,
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'pgross',
  created_by_name: null,
  retired: false,
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
       ['Forge', 'GitHub'],
      ['Owner', 'paul-gross'],
      ['Repository', 'blizzard'],
      ['Base branch', 'master'],
      ['Landing policy', 'merge on checks'],
      ['Created by', 'pgross'],
    ]);
    expect(vm.revision).toBe(2);
    expect(vm.links?.links.map((l) => l.name)).toEqual(['github', 'gh-blizzard']);
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
       forge: 'frg_github',
      owner: 'paul-gross',
      repo: 'blizzard',
      base_branch: 'master',
      secret_name: 'gh-blizzard',
    };
    expect(repositoryCreate(body)).toEqual({
      slug: 'blizzard',
       forge: 'frg_github',
      owner: 'paul-gross',
      repo: 'blizzard',
      base_branch: 'master',
      secret_name: 'gh-blizzard',
    });
    expect(repositoryCreate({ ...body, name: 'Blizzard' }).name).toBe('Blizzard');
  });
});

const POLICIES = [
  { policy: 'merge-on-checks', requires: ['checks', 'merge'], uses: [] },
  { policy: 'park', requires: ['merge-poll'], uses: ['checks'] },
  { policy: 'fast-forward', requires: ['ref-update'], uses: [] },
];

describe('landing policy choices', () => {
  it('offers the schema policies in its order, disabling one the forge cannot honour and naming what it lacks', () => {
    expect(landingPolicyOptions(POLICIES, ['checks', 'merge', 'ref-update'])).toEqual([
      { value: 'merge-on-checks', label: 'merge on checks' },
      { value: 'park', label: 'park — forge lacks merge-poll', disabled: true },
      { value: 'fast-forward', label: 'fast-forward' },
    ]);
  });

  it('disables nothing until a forge is chosen', () => {
    expect(landingPolicyOptions(POLICIES, null).some((option) => option.disabled)).toBe(false);
  });

  it('reads an unknown policy raw', () => {
    expect(landingPolicyOptions([{ policy: 'rebase', requires: [], uses: [] }], [])).toEqual([
      { value: 'rebase', label: 'rebase' },
    ]);
  });

  it('falls back to the generated policies, none disabled, when the schema is unreadable', () => {
    expect(landingPolicies(undefined)).toBeNull();
    expect(landingPolicies({})).toBeNull();
    expect(landingPolicies({ [LANDING_POLICIES_KEY]: [{ policy: 'park', requires: 'merge-poll' }] })).toBeNull();
    expect(landingPolicies({ [LANDING_POLICIES_KEY]: POLICIES })).toEqual(POLICIES);
    const fallback = landingPolicyOptions(null, []);
    expect(fallback.map((option) => option.value)).toEqual(['merge-on-checks', 'fast-forward', 'park']);
    expect(fallback.some((option) => option.disabled)).toBe(false);
  });

  it('fills the policy field in the repository form', () => {
    const fields = repositoryFields([], POLICIES, ['merge-poll']);
    expect(fields.find((field) => field.key === 'landing_policy')?.options?.[1]).toEqual({ value: 'park', label: 'park' });
  });
});

describe('chosenForgeOffered', () => {
  it('reads the chosen forge detail, null with none chosen or none read', () => {
    expect(chosenForgeOffered(null, undefined)).toBeNull();
    expect(chosenForgeOffered('frg_1', undefined)).toBeNull();
  });
});

describe('park timeout', () => {
  const FORM = {
    slug: 'b', name: '', forge: 'frg_1', owner: 'o', repo: 'r', base_branch: 'main', secret_name: 's',
    park_timeout_hours: '12',
  };

  it('shows only for park', () => {
    const shown = (policy: string) => shownFields(REPOSITORY_FIELDS, { landing_policy: policy }).map((field) => field.key);
    expect(shown('park')).toContain('park_timeout_hours');
    expect(shown('merge-on-checks')).not.toContain('park_timeout_hours');
    expect(shown('')).not.toContain('park_timeout_hours');
  });

  it('is sent as a number on a parking create, and never when hidden', () => {
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...FORM, landing_policy: 'park' }))).toMatchObject({
      landing_policy: 'park',
      park_timeout_hours: 12,
    });
    const hidden = repositoryCreate(createBody(REPOSITORY_FIELDS, { ...FORM, landing_policy: 'fast-forward' }));
    expect(hidden.landing_policy).toBe('fast-forward');
    expect(hidden).not.toHaveProperty('park_timeout_hours');
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...FORM, landing_policy: '' }))).not.toHaveProperty(
      'landing_policy',
    );
    expect(missingRequired(REPOSITORY_FIELDS, { ...FORM, landing_policy: 'park' })).toEqual([]);
  });
});

describe('repository detail policy facts', () => {
  const facts = (repository: RepositorySummary) =>
    Object.fromEntries(repositoryRecordVm(repository)!.facts.map((fact) => [fact.label, fact.value]));

  it('shows the policy, and the timeout only for park', () => {
    expect(facts(BLIZZARD)['Park timeout']).toBeUndefined();
    expect(facts({ ...BLIZZARD, landing_policy: 'park', park_timeout_hours: 6 })).toMatchObject({
      'Landing policy': 'park',
      'Park timeout': '6 h',
    });
    expect(facts({ ...BLIZZARD, landing_policy: 'park' })['Park timeout']).toBe('default');
  });

  it('reads an unknown stored policy raw', () => {
    expect(facts({ ...BLIZZARD, landing_policy: 'rebase' })['Landing policy']).toBe('rebase');
  });
});
