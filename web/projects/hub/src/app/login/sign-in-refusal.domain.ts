/** Why the hub turned a sign-in away: a refusal this build knows, or one it does not, kept raw. */
export type SignInRefusal =
  | 'invite_only'
  | 'invitation_unusable'
  | 'invitation_email_mismatch'
  | { readonly unknown: string };
