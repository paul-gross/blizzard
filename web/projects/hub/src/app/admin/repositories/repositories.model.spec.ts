import { describe, expect, it } from 'vitest';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { shownFieldViews } from '../config-field-tree.model';
import {
  landingPolicyLabel,
  honourForge,
  landingPolicyOptions,
  repositoryBadges,
  repositoryFieldViews,
  repositoryRecordVm,
  repositoryRows,
} from './repositories.model';
import { credentialChoice, forgeChoice, policyHonour, repository } from './repository.builders';
import { EMPTY_REPOSITORY_DRAFT, type LandingPolicy, type Repository } from './repository.domain';

const BLIZZARD = repository();
const OLD = repository({ id: 'repo_2', slug: 'old', name: 'Old one', retired: true });

describe('repositoryBadges', () => {
  it('badges only a retired repository', () => {
    expect(repositoryBadges(OLD)).toEqual([{ label: 'retired', tone: lifecycleTone(true) }]);
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

  it('titles a row by slug and shows a name that differs from it', () => {
    expect(repositoryRows([OLD], 'all')[0]).toMatchObject({ title: 'old', sub: ['Old one', 'paul-gross/blizzard', 'base master'] });
  });
});

describe('repositoryRecordVm', () => {
  it('answers null with none loaded', () => {
    expect(repositoryRecordVm(undefined)).toBeNull();
  });

  it('shows the fields, revision, and forge and credential links', () => {
    const vm = repositoryRecordVm(BLIZZARD)!;
    expect(vm.facts.map((f) => [f.label, f.value])).toEqual([
      ['Name', 'blizzard'],
      ['Forge', 'GitHub'],
      ['Owner', 'paul-gross'],
      ['Repository', 'blizzard'],
      ['Base branch', 'master'],
      ['Landing policy', 'merge on checks'],
      ['Allow no CI', 'No'],
      ['No CI policy', 'Only permits absent checks, not failing or pending checks. Snapshotted at the first turn of a visit; changes affect later visits.'],
      ['Created by', 'pgross'],
    ]);
    expect(vm.revision).toBe(2);
    expect(vm.links?.links).toEqual([
      { kind: 'forge', name: 'github', route: ['/admin', 'forges', 'frg_github'] },
      { kind: 'credential', name: 'gh-blizzard', route: ['/admin', 'credentials', 'cred_1'] },
    ]);
    expect(repositoryRecordVm(repository({ credential: null }))?.links?.links.map((l) => l.kind)).toEqual(['forge']);
    expect(vm.hasHistory).toBe(true);
  });

  it('shows who created it by name, falling back to the id', () => {
    const createdBy = (record: Repository) => repositoryRecordVm(record)!.facts.find((f) => f.label === 'Created by')?.value;
    expect(createdBy(repository({ createdByName: 'Paul Gross' }))).toBe('Paul Gross');
    expect(createdBy(BLIZZARD)).toBe('pgross');
  });

  it('notes a retired repository', () => {
    expect(repositoryRecordVm(OLD)?.note).toContain('Retired');
  });

  it('shows allow no CI', () => {
    expect(repositoryRecordVm(repository({ allowNoCi: true }))?.facts.find((f) => f.label === 'Allow no CI')?.value).toBe('Yes');
  });
});

describe('repository detail policy facts', () => {
  const facts = (record: Repository) =>
    Object.fromEntries(repositoryRecordVm(record)!.facts.map((fact) => [fact.label, fact.value]));

  it('shows the policy, and the timeout only for park', () => {
    expect(facts(BLIZZARD)['Park timeout']).toBeUndefined();
    expect(facts(repository({ landingPolicy: 'park', parkTimeoutHours: 6 }))).toMatchObject({
      'Landing policy': 'park',
      'Park timeout': '6 h',
    });
    expect(facts(repository({ landingPolicy: 'park' }))['Park timeout']).toBe('default');
  });

  it('reads an unlisted stored policy raw', () => {
    expect(facts(repository({ landingPolicy: { unlisted: 'rebase' } }))['Landing policy']).toBe('rebase');
  });
});

describe('landingPolicyLabel', () => {
  it('names each known policy and reads an unlisted one raw', () => {
    expect(landingPolicyLabel('merge-on-checks')).toBe('merge on checks');
    expect(landingPolicyLabel('fast-forward')).toBe('fast-forward');
    expect(landingPolicyLabel('park')).toBe('park');
    expect(landingPolicyLabel({ unlisted: 'rebase' })).toBe('rebase');
  });
});

const POLICIES: readonly LandingPolicy[] = ['merge-on-checks', 'park', 'fast-forward'];
const HONOUR_NO_POLL = [policyHonour('merge-on-checks'), policyHonour('park', ['merge-poll']), policyHonour('fast-forward')];

describe('landing policy choices', () => {
  it('offers the hub\'s policies in order, none disabled, with no forge chosen', () => {
    expect(landingPolicyOptions(POLICIES, null)).toEqual([
      { value: 'merge-on-checks', label: 'merge on checks' },
      { value: 'park', label: 'park' },
      { value: 'fast-forward', label: 'fast-forward' },
    ]);
  });

  it('enables every policy a forge honours', () => {
    expect(landingPolicyOptions(POLICIES, POLICIES.map((policy) => policyHonour(policy))).some((option) => option.disabled)).toBe(false);
  });

  it('disables a policy the forge cannot honour and names what it lacks', () => {
    expect(landingPolicyOptions(POLICIES, HONOUR_NO_POLL)).toEqual([
      { value: 'merge-on-checks', label: 'merge on checks' },
      { value: 'park', label: 'park — forge lacks merge-poll', disabled: true },
      { value: 'fast-forward', label: 'fast-forward' },
    ]);
  });

  it('falls back to the known policies, none disabled, while the list read is pending', () => {
    const fallback = landingPolicyOptions(null, null);
    expect(fallback.map((option) => option.value)).toEqual(['merge-on-checks', 'fast-forward', 'park']);
    expect(fallback.some((option) => option.disabled)).toBe(false);
  });

  it('reads an unlisted policy raw', () => {
    const rebase: LandingPolicy = { unlisted: 'rebase' };
    expect(landingPolicyOptions([rebase], [policyHonour(rebase, ['rebase-api'])])).toEqual([
      { value: 'rebase', label: 'rebase — forge lacks rebase-api', disabled: true },
    ]);
    expect(landingPolicyOptions([rebase], null)).toEqual([{ value: 'rebase', label: 'rebase' }]);
  });
});

describe('repositoryFieldViews', () => {
  const fields = repositoryFieldViews(
    [forgeChoice(), forgeChoice({ id: 'frg_old', retired: true })],
    [credentialChoice(), credentialChoice({ id: 'cred_old', retired: true })],
    POLICIES,
    HONOUR_NO_POLL,
  );
  const field = (key: string) => fields.find((view) => view.key === key);

  it('fills the forge, credential and policy choices, live ones only', () => {
    expect(field('forge')?.options).toEqual([{ value: 'frg_github', label: 'github · GitHub' }]);
    expect(field('credential')?.options).toEqual([{ value: 'cred_1', label: 'gh-blizzard · github.token', kind: 'frg_github' }]);
    expect(field('landingPolicy')?.options?.[1]).toEqual({ value: 'park', label: 'park — forge lacks merge-poll', disabled: true });
  });

  it('keys the credential choices by the chosen forge', () => {
    expect(field('credential')?.filterBy).toBe('forge');
  });

  it('draws the park timeout as hours or the default, shown only for park', () => {
    expect(field('parkTimeoutHours')).toMatchObject({ kind: 'number', orDefault: { key: 'parkTimeoutDefault', label: 'Default' } });
    const shown = (landingPolicy: string) =>
      shownFieldViews(fields, 'create', { ...EMPTY_REPOSITORY_DRAFT, landingPolicy }).map((view) => view.key);
    expect(shown('park')).toContain('parkTimeoutHours');
    expect(shown('merge-on-checks')).not.toContain('parkTimeoutHours');
    expect(shown('')).not.toContain('parkTimeoutHours');
  });

  it('offers every draft field but the default flag, which the timeout field carries', () => {
    expect(fields.map((view) => view.key)).toEqual([
      'slug',
      'name',
      'forge',
      'owner',
      'repo',
      'baseBranch',
      'credential',
      'landingPolicy',
      'allowNoCi',
      'parkTimeoutHours',
    ]);
  });
});

describe('honourForge', () => {
  it('names the draft\'s forge only while the form is open and names one', () => {
    expect(honourForge(true, { forge: 'frg_lab' })).toBe('frg_lab');
    expect(honourForge(true, { forge: '' })).toBeNull();
    expect(honourForge(false, { forge: 'frg_lab' })).toBeNull();
  });
});
