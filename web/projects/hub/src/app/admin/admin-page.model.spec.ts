import { describe, expect, it } from 'vitest';

import { assignRoleErrorText, pendingRoleUserIds } from './admin-page.model';

describe('assignRoleErrorText', () => {
  it('is null without an error', () => {
    expect(assignRoleErrorText(null)).toBeNull();
    expect(assignRoleErrorText(undefined)).toBeNull();
  });

  it("surfaces the refusal's own detail", () => {
    expect(assignRoleErrorText({ detail: 'Cannot demote the last superuser.' })).toBe('Cannot demote the last superuser.');
  });

  it('falls back to a generic line when the detail is not a string', () => {
    expect(assignRoleErrorText(new Error('network'))).toBe('Failed to change role.');
  });

  it('reads a list-shaped 422 as its field-named messages', () => {
    expect(assignRoleErrorText({ detail: [{ loc: ['body', 'role'], msg: 'bad', type: 'value_error' }] })).toBe('role: bad');
  });
});

describe('pendingRoleUserIds', () => {
  it('lists the user id of each pending assignment', () => {
    expect(pendingRoleUserIds([{ userId: 'u1' }, { userId: 'u2' }])).toEqual(['u1', 'u2']);
  });
});
