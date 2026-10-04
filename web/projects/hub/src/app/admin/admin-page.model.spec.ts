import { describe, expect, it } from 'vitest';

import { assignRoleErrorText } from './admin-page.model';

describe('assignRoleErrorText', () => {
  it('is null without an error', () => {
    expect(assignRoleErrorText(null)).toBeNull();
    expect(assignRoleErrorText(undefined)).toBeNull();
  });

  it("surfaces the refusal's own detail", () => {
    expect(assignRoleErrorText({ detail: 'Cannot demote the last superuser.' })).toBe('Cannot demote the last superuser.');
  });

  it('falls back to a generic line when the detail is not a string', () => {
    expect(assignRoleErrorText({ detail: [{ msg: 'bad' }] })).toBe('Failed to change role.');
    expect(assignRoleErrorText(new Error('network'))).toBe('Failed to change role.');
  });
});
