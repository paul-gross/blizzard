import { readRefusal } from 'fleet/core';
import type { hubApi } from 'fleet/hub-api';
import { credentialSummary, forgeDetail, repositorySummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { repository, repositoryDraft } from './repository.builders';
import { PARK_TIMEOUT_MESSAGE } from './repository.domain';
import {
  fromCredentialSummary,
  fromForgeSummary,
  fromLandingPolicyName,
  fromLandingPolicyNeeds,
  fromPolicyHonour,
  fromRepositorySummary,
  REPOSITORY_REFUSALS,
  toCreateRequest,
  toDraft,
  toEditBaseline,
  toEditRequest,
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

const BAD_HOURS = [
  ['blank', ''],
  ['malformed', '4.5'],
  ['signed', '-1'],
  ['zero', '0'],
  ['unsafe', '9007199254740992'],
  ['overflowing', '99999999999999999999'],
] as const;
const HOURS_REFUSED = { ok: false, fields: { parkTimeoutHours: PARK_TIMEOUT_MESSAGE } };

describe('toDraft', () => {
  it('starts an edit from every field of the repository', () => {
    expect(toDraft(repository({ name: 'Blizzard', allowNoCi: true }))).toEqual({
      slug: 'blizzard',
      name: 'Blizzard',
      forge: 'frg_github',
      owner: 'paul-gross',
      repo: 'blizzard',
      baseBranch: 'master',
      credential: 'cred_1',
      landingPolicy: 'merge-on-checks',
      allowNoCi: true,
      parkTimeoutDefault: true,
      parkTimeoutHours: '',
    });
  });

  it('opens an unset timeout as the default choice and a stated one as its hours', () => {
    expect(toDraft(repository({ landingPolicy: 'park' }))).toMatchObject({ parkTimeoutDefault: true, parkTimeoutHours: '' });
    expect(toDraft(repository({ landingPolicy: 'park', parkTimeoutHours: 6 }))).toMatchObject({
      parkTimeoutDefault: false,
      parkTimeoutHours: '6',
    });
  });

  it('keeps an unlisted policy key and reads no credential as none chosen', () => {
    expect(toDraft(repository({ landingPolicy: { unlisted: 'rebase' }, credential: null }))).toMatchObject({
      landingPolicy: 'rebase',
      credential: '',
    });
  });
});

describe('toEditBaseline', () => {
  it('captures the id, revision and draft from one read', () => {
    const read = repository({ revision: 7 });
    expect(toEditBaseline(read)).toEqual({ id: 'repo_1', revision: 7, draft: toDraft(read) });
  });
});

describe('toCreateRequest', () => {
  it('sends every required field, trimmed, and leaves a blank name out', () => {
    expect(toCreateRequest(repositoryDraft({ slug: ' blizzard ', owner: ' paul-gross ' }))).toEqual({
      ok: true,
      request: {
        slug: 'blizzard',
        forge: 'frg_github',
        owner: 'paul-gross',
        repo: 'blizzard',
        base_branch: 'master',
        credential: 'cred_1',
        allow_no_ci: false,
        landing_policy: 'merge-on-checks',
      },
    });
    expect(toCreateRequest(repositoryDraft({ name: ' Blizzard ' }))).toMatchObject({ request: { name: 'Blizzard' } });
  });

  it('sends allow no CI both ways', () => {
    expect(toCreateRequest(repositoryDraft({ allowNoCi: false }))).toMatchObject({ request: { allow_no_ci: false } });
    expect(toCreateRequest(repositoryDraft({ allowNoCi: true }))).toMatchObject({ request: { allow_no_ci: true } });
  });

  it('leaves a blank landing policy out, and no timeout when the policy does not park', () => {
    const blank = toCreateRequest(repositoryDraft({ landingPolicy: '', parkTimeoutDefault: false, parkTimeoutHours: 'x' }));
    expect(blank.ok && blank.request).not.toHaveProperty('landing_policy');
    expect(blank.ok && blank.request).not.toHaveProperty('park_timeout_hours');
    const merge = toCreateRequest(repositoryDraft({ parkTimeoutDefault: false, parkTimeoutHours: '12' }));
    expect(merge.ok && merge.request).not.toHaveProperty('park_timeout_hours');
  });

  it('sends the default choice as null and stated hours as their number', () => {
    expect(toCreateRequest(repositoryDraft({ landingPolicy: 'park' }))).toMatchObject({
      request: { landing_policy: 'park', park_timeout_hours: null },
    });
    expect(
      toCreateRequest(repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault: false, parkTimeoutHours: '12' })),
    ).toMatchObject({ request: { park_timeout_hours: 12 } });
  });

  it.each(BAD_HOURS)('answers %s stated hours as a field error and no request', (_name, hours) => {
    expect(
      toCreateRequest(repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault: false, parkTimeoutHours: hours })),
    ).toEqual(HOURS_REFUSED);
  });

  it('sends a credential and never a token secret', () => {
    const created = toCreateRequest(repositoryDraft());
    expect(created.ok && created.request.credential).toBe('cred_1');
    expect(created.ok && created.request).not.toHaveProperty('secret_name');
    expect(created.ok && created.request).not.toHaveProperty('token_secret');
  });
});

describe('toEditRequest', () => {
  const BEFORE = toDraft(repository({ name: 'Blizzard' }));
  const PARKED = toDraft(repository({ landingPolicy: 'park', parkTimeoutHours: 6 }));

  it('sends nothing for an unchanged draft, text compared trimmed', () => {
    expect(toEditRequest(BEFORE, { ...BEFORE, slug: ' blizzard ' })).toEqual({ ok: true, request: {} });
  });

  it.each([
    ['slug', { slug: 'core' }, { slug: 'core' }],
    ['name', { name: 'Core' }, { name: 'Core' }],
    ['forge', { forge: 'frg_lab' }, { forge: 'frg_lab' }],
    ['owner', { owner: 'acme' }, { owner: 'acme' }],
    ['repo', { repo: 'core' }, { repo: 'core' }],
    ['base branch', { baseBranch: 'main' }, { base_branch: 'main' }],
    ['credential', { credential: 'cred_2' }, { credential: 'cred_2' }],
    ['landing policy', { landingPolicy: 'fast-forward' }, { landing_policy: 'fast-forward' }],
    ['allow no CI on', { allowNoCi: true }, { allow_no_ci: true }],
  ] as const)('sends only a changed %s', (_name, change, body) => {
    expect(toEditRequest(BEFORE, { ...BEFORE, ...change })).toEqual({ ok: true, request: body });
  });

  it('sends allow no CI turned off as false', () => {
    const on = { ...BEFORE, allowNoCi: true };
    expect(toEditRequest(on, { ...on, allowNoCi: false })).toEqual({ ok: true, request: { allow_no_ci: false } });
  });

  it('keeps an unlisted stored policy unless it changes', () => {
    const rebase = toDraft(repository({ landingPolicy: { unlisted: 'rebase' } }));
    expect(toEditRequest(rebase, { ...rebase, name: 'x' })).toEqual({ ok: true, request: { name: 'x' } });
  });

  it('sends changed hours by value, and the same hours as no change', () => {
    expect(toEditRequest(PARKED, { ...PARKED, parkTimeoutHours: '48' })).toEqual({ ok: true, request: { park_timeout_hours: 48 } });
    expect(toEditRequest(PARKED, { ...PARKED, parkTimeoutHours: '06' })).toEqual({ ok: true, request: {} });
  });

  it('sends null only for a newly chosen default', () => {
    expect(toEditRequest(PARKED, { ...PARKED, parkTimeoutDefault: true })).toEqual({ ok: true, request: { park_timeout_hours: null } });
    const unset = toDraft(repository({ landingPolicy: 'park' }));
    expect(toEditRequest(unset, { ...unset, name: 'x' })).toEqual({ ok: true, request: { name: 'x' } });
    expect(toEditRequest(unset, { ...unset, parkTimeoutDefault: false, parkTimeoutHours: '3' })).toEqual({
      ok: true,
      request: { park_timeout_hours: 3 },
    });
  });

  it.each(BAD_HOURS)('answers %s stated hours as a field error and no request', (_name, hours) => {
    expect(toEditRequest(PARKED, { ...PARKED, parkTimeoutHours: hours })).toEqual(HOURS_REFUSED);
  });

  it('neither judges nor sends the timeout once the policy stops parking', () => {
    expect(toEditRequest(PARKED, { ...PARKED, landingPolicy: 'merge-on-checks', parkTimeoutHours: 'x' })).toEqual({
      ok: true,
      request: { landing_policy: 'merge-on-checks' },
    });
  });
});

describe('REPOSITORY_REFUSALS', () => {
  it('lands each field refusal on its draft field', () => {
    const entries = [
      ['slug', 'slug'],
      ['name', 'name'],
      ['forge', 'forge'],
      ['owner', 'owner'],
      ['repo', 'repo'],
      ['base_branch', 'baseBranch'],
      ['credential', 'credential'],
      ['landing_policy', 'landingPolicy'],
      ['allow_no_ci', 'allowNoCi'],
      ['park_timeout_hours', 'parkTimeoutHours'],
    ] as const;
    for (const [loc, key] of entries) {
      expect(readRefusal({ type: 'urn:blizzard:problem:config-field-invalid', detail: [{ loc: ['body', loc], msg: 'bad' }] }, REPOSITORY_REFUSALS)).toEqual({
        ok: false,
        fields: { [key]: 'bad' },
        form: null,
      });
    }
  });

  it('lands a taken slug on slug and a taken owner/repo on repo', () => {
    expect(readRefusal({ type: 'urn:blizzard:problem:repository-slug-taken', detail: 'Slug taken.' }, REPOSITORY_REFUSALS)).toEqual({
      ok: false,
      fields: { slug: 'Slug taken.' },
      form: null,
    });
    expect(
      readRefusal({ type: 'urn:blizzard:problem:repository-coordinate-taken', detail: 'Already registered.' }, REPOSITORY_REFUSALS),
    ).toEqual({ ok: false, fields: { repo: 'Already registered.' }, form: null });
  });

  it('lands a revision conflict and an unknown path on the form line', () => {
    expect(readRefusal({ type: 'urn:blizzard:problem:revision-conflict', detail: 'Moved on.' }, REPOSITORY_REFUSALS)).toEqual({
      ok: false,
      fields: {},
      form: 'Moved on.',
    });
    expect(readRefusal({ detail: [{ loc: ['body', 'constructor'], msg: 'x' }] }, REPOSITORY_REFUSALS)).toEqual({
      ok: false,
      fields: {},
      form: 'constructor: x',
    });
  });
});
