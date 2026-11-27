import { describe, expect, it } from 'vitest';
import type { MeResponse } from 'fleet/hub-api';
import { meResponse, meTenant } from 'fleet/testing';

import { fromMeResponse, fromMeTenant } from './session.translate';

describe('fromMeResponse', () => {
  it('carries the identity, its permissions, and its memberships', () => {
    const wire = meResponse({
      user_id: 'usr_7',
      username: 'ada',
      display_name: 'Ada',
      role: 'admin',
      permissions: ['fleet:view', 'user:manage'],
      tenants: [meTenant({ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view'] })],
    });
    expect(fromMeResponse(wire)).toEqual({
      user_id: 'usr_7',
      username: 'ada',
      display_name: 'Ada',
      permissions: ['fleet:view', 'user:manage'],
      tenants: [{ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view'] }],
      tenant_resolved: true,
    });
  });

  it('drops a permission the build cannot name, in the identity and in a membership', () => {
    const wire = {
      ...meResponse({ permissions: ['fleet:view'] }),
      permissions: ['fleet:view', 'future:power'],
      tenants: [{ ...meTenant(), permissions: ['future:power', 'chunk:control'] }],
    } as unknown as MeResponse;
    const session = fromMeResponse(wire);
    expect(session.permissions).toEqual(['fleet:view']);
    expect(session.tenants[0].permissions).toEqual(['chunk:control']);
  });

  it('reads a read the hub resolved no tenant for as unresolved', () => {
    expect(fromMeResponse(meResponse({ role: null })).tenant_resolved).toBe(false);
  });

  it('reads absent or empty memberships as none', () => {
    expect(fromMeResponse(meResponse({ tenants: [] })).tenants).toEqual([]);
    expect(fromMeResponse({ ...meResponse(), tenants: null } as unknown as MeResponse).tenants).toEqual([]);
  });
});

describe('fromMeTenant', () => {
  it('carries the tenant id, name and permissions, without the role', () => {
    expect(fromMeTenant(meTenant({ tenant_id: 'ten_b', name: 'Beta', role: 'guest', permissions: ['fleet:view'] }))).toEqual({
      tenant_id: 'ten_b',
      name: 'Beta',
      permissions: ['fleet:view'],
    });
  });
});
