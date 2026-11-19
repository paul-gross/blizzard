/** Roles this build understands; only these can be assigned. */
export type MemberRole = 'pending' | 'guest' | 'contributor' | 'admin' | 'superuser';
export type HeldMemberRole = MemberRole | { readonly unlisted: string };

export interface Member {
  readonly userId: string;
  readonly username: string;
  readonly displayName: string;
  readonly email: string | null;
  readonly role: HeldMemberRole;
  readonly hubAdmin: boolean;
  readonly createdAt: string;
  readonly identities: readonly { readonly providerName: string; readonly handle: string }[];
  readonly assignableRoles: readonly MemberRole[];
  readonly revocable: boolean;
}

export function memberRoleLabel(role: HeldMemberRole): string {
  return typeof role === 'string' ? role : role.unlisted;
}

/** Whether this is the signed-in actor's own membership. */
export function isOwnMember(member: Member, currentUserId: string | null): boolean {
  return member.userId === currentUserId;
}

/** The hub's offered roles govern changes, except that the actor's own membership is read-only. */
export function canChangeRole(member: Member, currentUserId: string | null): boolean {
  return !isOwnMember(member, currentUserId) && member.assignableRoles.some((role) => role !== member.role);
}

/** Revocation requires the hub's permission and cannot target the actor's own membership. */
export function canRevoke(member: Member, currentUserId: string | null): boolean {
  return !isOwnMember(member, currentUserId) && member.revocable;
}
