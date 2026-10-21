import { hubApi } from 'fleet';

import { refusalText } from './sign-in-refusal';

describe('refusalText', () => {
  it('has nothing to say without a refusal', () => {
    expect(refusalText(null)).toBeNull();
  });

  it('tells an uninvited newcomer to reach out for access', () => {
    expect(refusalText(hubApi.SignInRefusal.INVITE_ONLY)).toBe(
      "You don't have access to anything here yet. Reach out to your administrator for access.",
    );
  });

  it('tells the holder of an expired, revoked or used invitation to ask for a new one', () => {
    expect(refusalText(hubApi.SignInRefusal.INVITATION_UNUSABLE)).toBe(
      'This invitation is no longer valid. Reach out to your administrator for a new one.',
    );
  });

  it('tells a person signed in with the wrong account which account the invitation needs', () => {
    expect(refusalText(hubApi.SignInRefusal.INVITATION_EMAIL_MISMATCH)).toBe(
      'This invitation is for a different email address. Sign in with the account that uses it, or reach out to your administrator.',
    );
  });

  it('reads a code it does not know, or one shaped like an object key, as invite-only', () => {
    expect(refusalText('something_new')).toBe(refusalText(hubApi.SignInRefusal.INVITE_ONLY));
    expect(refusalText('toString')).toBe(refusalText(hubApi.SignInRefusal.INVITE_ONLY));
  });
});
