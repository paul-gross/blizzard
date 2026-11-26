import { openLookup } from 'fleet/core';
import { type MemberView, type hubApi } from 'fleet/hub-api';
import type { HeldMemberRole, Member, MemberRole } from './members.domain';

const roleOf = openLookup<hubApi.Role, HeldMemberRole>(
  { pending: 'pending', guest: 'guest', contributor: 'contributor', admin: 'admin', superuser: 'superuser' },
  (unlisted) => ({ unlisted }),
);

export function fromMemberView(wire: MemberView): Member {
  return {
    userId: wire.user_id, username: wire.username, displayName: wire.display_name, email: wire.email,
    role: roleOf(wire.role), hubAdmin: wire.hub_admin, createdAt: wire.created_at,
    identities: wire.identities.map((identity) => ({ providerName: identity.provider_name, handle: identity.handle })),
    assignableRoles: (wire.assignable_roles ?? []).map(roleOf).filter((role): role is MemberRole => typeof role === 'string'),
    revocable: wire.revocable,
  };
}

export function toMemberRoleRequest(role: MemberRole): { role: hubApi.Role } {
  return { role };
}
