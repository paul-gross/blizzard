/** Why the hub turned a sign-in away, as it names it in `/login?refusal=<code>`. Nothing is written in any case. */
export type SignInRefusal = 'invite_only' | 'invitation_unusable' | 'invitation_email_mismatch';

const NO_ACCESS = "You don't have access to anything here yet. Reach out to your administrator for access.";

const TEXTS: Readonly<Record<SignInRefusal, string>> = {
  // A first sign-in by someone the hub does not know, with no invitation: the hub is invite-only.
  invite_only: NO_ACCESS,
  // The invitation expired, was revoked, or was already used.
  invitation_unusable: 'This invitation is no longer valid. Reach out to your administrator for a new one.',
  // No verified email on the account matches the invitation's; the invitation stays live.
  invitation_email_mismatch:
    'This invitation is for a different email address. Sign in with the account that uses it, or reach out to your administrator.',
};

/** The sign-in page's text for a refusal `code`, `null` when there is none. A code this board does not know reads as
 * `invite_only`, the refusal that admits nothing. */
export function refusalText(code: string | null): string | null {
  if (code === null) return null;
  return Object.hasOwn(TEXTS, code) ? TEXTS[code as SignInRefusal] : TEXTS.invite_only;
}
