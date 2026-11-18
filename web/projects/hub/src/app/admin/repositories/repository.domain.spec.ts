import { describe, expect, it } from 'vitest';

import { credentialChoice, forgeChoice, policyHonour, repository } from './repository.builders';
import { honourFor, landingPolicyKey, liveCredentials, liveForges, nameRepeatsSlug, parks } from './repository.domain';

describe('landingPolicyKey', () => {
  it('reads a known policy as its name and an unlisted one raw', () => {
    expect(landingPolicyKey('park')).toBe('park');
    expect(landingPolicyKey({ unlisted: 'rebase' })).toBe('rebase');
  });
});

describe('parks', () => {
  it('holds only for park', () => {
    expect(parks('park')).toBe(true);
    expect(parks('merge-on-checks')).toBe(false);
    expect(parks('fast-forward')).toBe(false);
    expect(parks({ unlisted: 'park-later' })).toBe(false);
  });
});

describe('nameRepeatsSlug', () => {
  it('holds when the name is the slug', () => {
    expect(nameRepeatsSlug(repository())).toBe(true);
    expect(nameRepeatsSlug(repository({ name: 'Blizzard' }))).toBe(false);
  });
});

describe('honourFor', () => {
  const honour = [policyHonour('park', ['merge-poll']), policyHonour({ unlisted: 'rebase' })];

  it('finds a known and an unlisted policy, and nothing for one the list omits', () => {
    expect(honourFor(honour, 'park')?.honoured).toBe(false);
    expect(honourFor(honour, { unlisted: 'rebase' })?.honoured).toBe(true);
    expect(honourFor(honour, 'fast-forward')).toBeUndefined();
  });
});

describe('live choices', () => {
  it('drops retired forges and credentials', () => {
    expect(liveForges([forgeChoice(), forgeChoice({ id: 'frg_old', retired: true })]).map((f) => f.id)).toEqual(['frg_github']);
    expect(liveCredentials([credentialChoice(), credentialChoice({ id: 'cred_old', retired: true })]).map((c) => c.id)).toEqual([
      'cred_1',
    ]);
  });
});
