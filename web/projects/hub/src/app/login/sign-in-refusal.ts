import { hubApi, openLookup } from 'fleet';

const NO_ACCESS = "You don't have access to anything here yet. Reach out to your administrator for access.";

const TEXTS = openLookup<hubApi.SignInRefusal, string>(
  {
    // A first sign-in by someone the hub does not know, with no invitation: the hub is invite-only.
    [hubApi.SignInRefusal.INVITE_ONLY]: NO_ACCESS,
    // The invitation expired, was revoked, or was already used.
    [hubApi.SignInRefusal.INVITATION_UNUSABLE]: 'This invitation is no longer valid. Reach out to your administrator for a new one.',
    // No verified email on the account matches the invitation's; the invitation stays live.
    [hubApi.SignInRefusal.INVITATION_EMAIL_MISMATCH]:
      'This invitation is for a different email address. Sign in with the account that uses it, or reach out to your administrator.',
  },
  // A code this board does not know reads as `invite_only`, the refusal that admits nothing.
  () => NO_ACCESS,
);

/** The sign-in page's text for a refusal `code`, `null` when there is none. */
export function refusalText(code: string | null): string | null {
  return code === null ? null : TEXTS(code);
}
