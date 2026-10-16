import { type MemberView, type hubApi, errorMessage } from 'fleet';

/** A refused membership change's message through `errorMessage` (its 422 handling included), a generic failure line
 * when it carries none, or `null` with no error. */
export function memberChangeErrorText(error: unknown): string | null {
  if (!error) return null;
  return errorMessage(error, 'Failed to change the membership.');
}

/** The user ids a membership change is in flight for, from the pending mutations' variables. */
export function pendingMemberIds(...pending: readonly (readonly { readonly userId: string }[])[]): readonly string[] {
  return pending.flatMap((vars) => vars.map((v) => v.userId));
}

/** The roles a member's selector offers: the roles the hub lets the actor give them, in the hub's order, with the role
 * they hold first when the hub does not list it. */
export function roleChoices(member: MemberView): readonly hubApi.Role[] {
  const assignable = member.assignable_roles ?? [];
  return assignable.includes(member.role) ? assignable : [member.role, ...assignable];
}

/** Whether the actor may give `member` another role: never their own, and only when the hub offers one. */
export function canChangeRole(member: MemberView, currentUserId: string | null): boolean {
  return member.user_id !== currentUserId && roleChoices(member).length > 1;
}

/** Whether the actor may revoke `member`'s membership: never their own, and only when the hub says so. */
export function canRevoke(member: MemberView, currentUserId: string | null): boolean {
  return member.user_id !== currentUserId && member.revocable === true;
}

/** What the revocation dialog asks before ending `member`'s membership; `null` with no member. */
export function revokePrompt(member: MemberView | null) {
  if (member === null) return null;
  return {
    heading: 'Revoke membership',
    message: `${member.display_name} (${member.username}) loses access to this tenant. An invitation brings them back.`,
    confirmLabel: 'Revoke',
    variant: 'danger' as const,
  };
}
