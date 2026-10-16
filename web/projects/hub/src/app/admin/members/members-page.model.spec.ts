import { describe, expect, it } from 'vitest';

import type { MemberView } from 'fleet';
import {
  canChangeRole,
  canRevoke,
  memberChangeErrorText,
  pendingMemberIds,
  revokePrompt,
  roleChoices,
} from './members-page.model';

const GRACE: MemberView = {
  user_id: 'usr_guest',
  username: 'grace',
  display_name: 'Grace',
  email: null,
  role: 'guest',
  hub_admin: false,
  created_at: '2026-07-21T00:00:00Z',
  assignable_roles: ['guest', 'contributor', 'admin'],
  revocable: true,
};

describe('memberChangeErrorText', () => {
  it('is null without an error', () => {
    expect(memberChangeErrorText(null)).toBeNull();
    expect(memberChangeErrorText(undefined)).toBeNull();
  });

  it("reads the hub's own detail", () => {
    expect(memberChangeErrorText({ detail: 'a tenant keeps at least one admin' })).toBe('a tenant keeps at least one admin');
  });

  it('falls back to a generic line when the error carries no detail', () => {
    expect(memberChangeErrorText(new Error('network'))).toBe('Failed to change the membership.');
  });

  it('formats a 422 validation detail', () => {
    expect(memberChangeErrorText({ detail: [{ loc: ['body', 'role'], msg: 'bad', type: 'value_error' }] })).toBe('role: bad');
  });
});

describe('pendingMemberIds', () => {
  it('gathers the user ids of every pending change', () => {
    expect(pendingMemberIds([{ userId: 'a' }], [{ userId: 'b' }, { userId: 'c' }])).toEqual(['a', 'b', 'c']);
  });
});

describe('roleChoices', () => {
  it("offers the hub's assignable roles in its order", () => {
    expect(roleChoices(GRACE)).toEqual(['guest', 'contributor', 'admin']);
  });

  it('puts the held role first when the hub does not list it', () => {
    expect(roleChoices({ ...GRACE, role: 'admin', assignable_roles: ['guest', 'contributor'] })).toEqual([
      'admin',
      'guest',
      'contributor',
    ]);
  });
});

describe('canChangeRole / canRevoke', () => {
  it('admits a change and a revocation the hub offers', () => {
    expect(canChangeRole(GRACE, 'usr_admin')).toBe(true);
    expect(canRevoke(GRACE, 'usr_admin')).toBe(true);
  });

  it('never admits either on your own row', () => {
    expect(canChangeRole(GRACE, 'usr_guest')).toBe(false);
    expect(canRevoke(GRACE, 'usr_guest')).toBe(false);
  });

  it('admits neither when the hub offers nothing', () => {
    const lastAdmin = { ...GRACE, role: 'admin' as const, assignable_roles: ['admin' as const], revocable: false };
    expect(canChangeRole(lastAdmin, 'usr_admin')).toBe(false);
    expect(canRevoke(lastAdmin, 'usr_admin')).toBe(false);
    expect(canChangeRole({ ...GRACE, assignable_roles: undefined }, 'usr_admin')).toBe(false);
    expect(canRevoke({ ...GRACE, revocable: undefined }, 'usr_admin')).toBe(false);
  });
});

describe('revokePrompt', () => {
  it('is null with no member', () => {
    expect(revokePrompt(null)).toBeNull();
  });

  it('names the member and the tenant', () => {
    const prompt = revokePrompt(GRACE)!;
    expect(prompt.message).toContain('Grace (grace)');
    expect(prompt.message).toContain('this tenant');
    expect(prompt.variant).toBe('danger');
  });

  it('names no tenant', () => {
    expect(revokePrompt(GRACE)!.message).toContain('loses access to this tenant');
  });
});
