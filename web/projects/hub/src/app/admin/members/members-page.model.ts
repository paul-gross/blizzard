import { errorMessage } from 'fleet';
import { type Member, type MemberRole } from './members.domain';

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
export function roleChoices(member: Member): readonly MemberRole[] {
  const assignable = member.assignableRoles;
  return typeof member.role !== 'string' || assignable.includes(member.role) ? assignable : [member.role, ...assignable];
}

/** What the revocation dialog asks before ending `member`'s membership; `null` with no member. */
export function revokePrompt(member: Member | null) {
  if (member === null) return null;
  return {
    heading: 'Revoke membership',
    message: `${member.displayName} (${member.username}) loses access to this tenant. An invitation brings them back.`,
    confirmLabel: 'Revoke',
    variant: 'danger' as const,
  };
}
