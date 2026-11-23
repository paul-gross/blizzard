import type { Membership, Session } from './session.domain';

export function membership(overrides: Partial<Membership> = {}): Membership {
  return { tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view'], ...overrides };
}

export function session(overrides: Partial<Session> = {}): Session {
  return {
    user_id: 'usr_1',
    username: 'ada',
    display_name: 'Ada',
    permissions: ['fleet:view'],
    tenants: [membership()],
    tenant_resolved: true,
    ...overrides,
  };
}
