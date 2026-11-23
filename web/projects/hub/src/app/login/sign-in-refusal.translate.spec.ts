import { describe, expect, it } from 'vitest';

import { fromRefusalCode } from './sign-in-refusal.translate';

describe('fromRefusalCode', () => {
  it('names each refusal the build knows', () => {
    expect(fromRefusalCode('invite_only')).toBe('invite_only');
    expect(fromRefusalCode('invitation_unusable')).toBe('invitation_unusable');
    expect(fromRefusalCode('invitation_email_mismatch')).toBe('invitation_email_mismatch');
  });

  it('keeps a code it does not know raw, one shaped like an object key included', () => {
    expect(fromRefusalCode('something_new')).toEqual({ unknown: 'something_new' });
    expect(fromRefusalCode('toString')).toEqual({ unknown: 'toString' });
  });
});
