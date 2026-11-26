import type { MemberView, hubApi } from 'fleet/hub-api';
import { memberRoleLabel, type Member } from './members.domain';

/** Encode a domain fixture as a served row for transport stubs. */
export function memberWire(member: Member): MemberView {
  return {
    user_id: member.userId, username: member.username, display_name: member.displayName, email: member.email,
    role: memberRoleLabel(member.role) as hubApi.Role, hub_admin: member.hubAdmin, created_at: member.createdAt,
    identities: member.identities.map((identity) => ({ provider_name: identity.providerName, handle: identity.handle })),
    assignable_roles: [...member.assignableRoles], revocable: member.revocable,
  };
}
