import { unlisted } from 'fleet';

import type { SignInRefusal } from './sign-in-refusal.domain';
import { fromRefusalCode } from './sign-in-refusal.translate';

const NO_ACCESS = "You don't have access to anything here yet. Reach out to your administrator for access.";

/** The sign-in page's copy for a refusal. */
export function refusalCopy(refusal: SignInRefusal): string {
  // A code this board does not know reads raw, never as a known refusal's copy.
  if (typeof refusal !== 'string') return refusedRaw(refusal.unknown);
  switch (refusal) {
    // A first sign-in by someone the hub does not know, with no invitation: the hub is invite-only.
    case 'invite_only':
      return NO_ACCESS;
    // The invitation expired, was revoked, or was already used.
    case 'invitation_unusable':
      return 'This invitation is no longer valid. Reach out to your administrator for a new one.';
    // No verified email on the account matches the invitation's; the invitation stays live.
    case 'invitation_email_mismatch':
      return 'This invitation is for a different email address. Sign in with the account that uses it, or reach out to your administrator.';
    default:
      return refusedRaw(unlisted(refusal));
  }
}

function refusedRaw(code: string): string {
  return `Sign-in was refused (${code}). Reach out to your administrator.`;
}

/** The sign-in page's text for a refusal `code`, `null` when there is none. */
export function refusalText(code: string | null): string | null {
  return code === null ? null : refusalCopy(fromRefusalCode(code));
}
