import { describe, expect, it } from 'vitest';
import { member } from './members.builders';
import { canChangeRole, canRevoke, isOwnMember } from './members.domain';

describe('membership judgments', () => {
  const row = member({ userId: 'usr_ada', role: 'guest', assignableRoles: ['guest', 'admin'], revocable: true });

  it('never changes or revokes the actor’s own membership even when offered by the hub', () => {
    expect(isOwnMember(row, 'usr_ada')).toBe(true);
    expect(canChangeRole(row, 'usr_ada')).toBe(false);
    expect(canRevoke(row, 'usr_ada')).toBe(false);
  });

  it('defers to the hub’s offered actions on another row', () => {
    expect(isOwnMember(row, null)).toBe(false);
    expect(canChangeRole(row, 'usr_other')).toBe(true);
    expect(canRevoke(row, 'usr_other')).toBe(true);
    expect(canChangeRole({ ...row, assignableRoles: ['guest'] }, 'usr_other')).toBe(false);
    expect(canRevoke({ ...row, revocable: false }, 'usr_other')).toBe(false);
  });

  it('allows only known offered roles when the held role is unlisted', () => {
    const unknown = { ...row, role: { unlisted: 'future' } };
    expect(canChangeRole(unknown, 'usr_other')).toBe(true);
    expect(canChangeRole({ ...unknown, assignableRoles: [] }, 'usr_other')).toBe(false);
  });
});
