import type { hubApi } from 'fleet';
import { credentialSummary, forgeDetail, repositorySummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { createBody } from '../config-edit.model';
import { REPOSITORY_FIELDS } from './repositories.model';
import {
  fromCredentialSummary,
  fromForgeSummary,
  fromLandingPolicyName,
  fromLandingPolicyNeeds,
  fromPolicyHonour,
  fromRepositorySummary,
  repositoryCreate,
} from './repository.translate';

describe('fromLandingPolicyName', () => {
  it('keeps a known policy and an unlisted one raw', () => {
    expect(fromLandingPolicyName('merge-on-checks')).toBe('merge-on-checks');
    expect(fromLandingPolicyName('fast-forward')).toBe('fast-forward');
    expect(fromLandingPolicyName('park')).toBe('park');
    expect(fromLandingPolicyName('rebase')).toEqual({ unlisted: 'rebase' });
  });
});

describe('fromRepositorySummary', () => {
  it('reads every field the board shows', () => {
    const wire = repositorySummary({
      landing_policy: 'park',
      park_timeout_hours: 6,
      credential_id: 'cred_1',
      credential_slug: 'gh-main',
      created_by_name: 'Ada',
      allow_no_ci: true,
    });
    expect(fromRepositorySummary(wire)).toEqual({
      id: 'repo_c',
      slug: 'core',
      name: 'core',
      forge: { id: 'frg_g', slug: 'github', name: 'GitHub' },
      credential: { id: 'cred_1', slug: 'gh-main' },
      owner: 'acme',
      repo: 'core',
      baseBranch: 'main',
      landingPolicy: 'park',
      allowNoCi: true,
      parkTimeoutHours: 6,
      retired: false,
      revision: 1,
      createdBy: 'usr_1',
      createdByName: 'Ada',
    });
  });

  it('reads no credential, a default timeout, and an unlisted policy', () => {
    const repository = fromRepositorySummary(repositorySummary({ landing_policy: 'rebase' }));
    expect(repository.credential).toBeNull();
    expect(repository.parkTimeoutHours).toBeNull();
    expect(repository.landingPolicy).toEqual({ unlisted: 'rebase' });
  });
});

describe('choice translators', () => {
  it('reads a forge choice', () => {
    expect(fromForgeSummary(forgeDetail({ retired: true }))).toEqual({ id: 'frg_g', slug: 'github', name: 'GitHub', retired: true });
  });

  it('reads a credential choice', () => {
    expect(fromCredentialSummary(credentialSummary())).toEqual({
      id: 'cred_g',
      slug: 'gh-main',
      binding: 'github.token',
      forgeId: 'frg_g',
      retired: false,
    });
  });

  it('reads the landing-policy list in order, an unlisted policy raw', () => {
    const needs = [
      { policy: 'park', requires: ['merge-poll'], uses: [] },
      { policy: 'rebase', requires: [], uses: [] },
    ] as unknown as hubApi.LandingPolicyNeedsView[];
    expect(fromLandingPolicyNeeds(needs)).toEqual(['park', { unlisted: 'rebase' }]);
  });

  it('reads a forge honour entry', () => {
    expect(fromPolicyHonour({ policy: 'park', honoured: false, missing: ['merge-poll'] })).toEqual({
      policy: 'park',
      honoured: false,
      missing: ['merge-poll'],
    });
  });
});

describe('repositoryCreate', () => {
  const FORM = {
    slug: 'blizzard',
    name: '',
    forge: 'frg_github',
    owner: 'paul-gross',
    repo: 'blizzard',
    base_branch: 'master',
    credential: 'cred_1',
  };

  it('leaves an empty name out', () => {
    expect(repositoryCreate(FORM)).toEqual({
      slug: 'blizzard',
      forge: 'frg_github',
      owner: 'paul-gross',
      repo: 'blizzard',
      base_branch: 'master',
      credential: 'cred_1',
    });
    expect(repositoryCreate({ ...FORM, name: 'Blizzard' }).name).toBe('Blizzard');
  });

  it('sends allow no CI both ways', () => {
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...FORM, allow_no_ci: false })).allow_no_ci).toBe(false);
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...FORM, allow_no_ci: true })).allow_no_ci).toBe(true);
  });

  it('sends the park timeout as a number on a parking create, never when hidden, and no blank policy', () => {
    const form = { ...FORM, park_timeout_hours: '12' };
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...form, landing_policy: 'park' }))).toMatchObject({
      landing_policy: 'park',
      park_timeout_hours: 12,
    });
    const hidden = repositoryCreate(createBody(REPOSITORY_FIELDS, { ...form, landing_policy: 'fast-forward' }));
    expect(hidden.landing_policy).toBe('fast-forward');
    expect(hidden).not.toHaveProperty('park_timeout_hours');
    expect(repositoryCreate(createBody(REPOSITORY_FIELDS, { ...form, landing_policy: '' }))).not.toHaveProperty('landing_policy');
  });
});
