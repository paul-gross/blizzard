import { type hubApi, openLookup } from 'fleet';

import type { SignInRefusal } from './sign-in-refusal.domain';

const refusalOf = openLookup<hubApi.SignInRefusal, SignInRefusal>(
  {
    invite_only: 'invite_only',
    invitation_unusable: 'invitation_unusable',
    invitation_email_mismatch: 'invitation_email_mismatch',
  },
  (unknown) => ({ unknown }),
);

/** The refusal a `/login?refusal=<code>` names; a code this build does not know keeps its raw value. */
export function fromRefusalCode(code: string): SignInRefusal {
  return refusalOf(code);
}
