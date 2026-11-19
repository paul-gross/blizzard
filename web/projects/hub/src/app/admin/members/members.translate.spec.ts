import { describe, expect, it } from 'vitest';
import type { MemberView } from 'fleet';
import { canChangeRole } from './members.domain';
import { member } from './members.builders';
import { memberWire } from './members-fixture.translate';
import { fromMemberView, toMemberRoleRequest } from './members.translate';
import { roleChoices } from './members-page.model';

describe('member boundary', () => {
  it('translates every served field independently', () => {
    const wire: MemberView = {
      user_id: 'usr_wire', username: 'lin', display_name: 'Lin Example', email: 'lin@example.test',
      role: 'contributor', hub_admin: true, created_at: '2026-11-19T09:12:34Z',
      identities: [{ provider_name: 'github', handle: 'lin-gh' }, { provider_name: 'gitlab', handle: 'lin-gl' }],
      assignable_roles: ['admin', 'guest'], revocable: true,
    };
    expect(fromMemberView(wire)).toEqual({
      userId: 'usr_wire', username: 'lin', displayName: 'Lin Example', email: 'lin@example.test',
      role: 'contributor', hubAdmin: true, createdAt: '2026-11-19T09:12:34Z',
      identities: [{ providerName: 'github', handle: 'lin-gh' }, { providerName: 'gitlab', handle: 'lin-gl' }],
      assignableRoles: ['admin', 'guest'], revocable: true,
    });
  });

  it('preserves an unlisted held role and excludes unlisted assignable roles', () => {
    const wire = memberWire(member({ role: { unlisted: 'future' }, assignableRoles: ['guest', 'admin'] }));
    wire.assignable_roles = [wire.role, ...wire.assignable_roles];
    const result = fromMemberView(wire);
    expect(result.role).toEqual({ unlisted: 'future' });
    expect(result.assignableRoles).toEqual(['guest', 'admin']);
    expect(roleChoices(result)).toEqual(['guest', 'admin']);
    expect(canChangeRole(result, 'another')).toBe(true);
    expect(canChangeRole(result, result.userId)).toBe(false);
  });

  it('encodes known roles for requests', () => {
    expect(toMemberRoleRequest('contributor')).toEqual({ role: 'contributor' });
  });
});
