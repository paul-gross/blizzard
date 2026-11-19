import type { Member } from './members.domain';

export function member(overrides: Partial<Member> = {}): Member {
  return { userId: 'usr_1', username: 'ada', displayName: 'Ada', email: null, role: 'guest', hubAdmin: false,
    createdAt: '2026-07-21T00:00:00Z', identities: [], assignableRoles: [], revocable: false, ...overrides };
}
