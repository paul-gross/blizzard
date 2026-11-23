import { describe, expect, it } from 'vitest';

import { session, membership } from './session.builders';
import { hasPermission, inSelectedTenant, resolveTenant } from './session.domain';

const ACME = membership({ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view', 'user:manage'] });
const BETA = membership({ tenant_id: 'ten_b', name: 'Beta', permissions: ['fleet:view'] });

describe('resolveTenant', () => {
  it('has nothing to enter without a membership', () => {
    expect(resolveTenant([], 'ten_a', 'ten_a')).toEqual({ kind: 'none' });
  });

  it('selects the only membership whatever the tab or the browser remembered', () => {
    expect(resolveTenant([ACME], null, null)).toEqual({
      kind: 'selected',
      tenant: ACME,
      tenants: [ACME],
    });
    expect(resolveTenant([ACME], 'ten_gone', 'ten_gone')).toEqual({
      kind: 'selected',
      tenant: ACME,
      tenants: [ACME],
    });
  });

  it("keeps the tab's tenant while it is still a membership, ahead of the browser's last choice", () => {
    expect(resolveTenant([ACME, BETA], 'ten_b', 'ten_a')).toMatchObject({
      kind: 'selected',
      tenant: BETA,
    });
  });

  it("falls back to the browser's last choice while it is still a membership", () => {
    expect(resolveTenant([ACME, BETA], 'ten_gone', 'ten_a')).toMatchObject({
      kind: 'selected',
      tenant: ACME,
    });
    expect(resolveTenant([ACME, BETA], null, 'ten_b')).toMatchObject({
      kind: 'selected',
      tenant: BETA,
    });
  });

  it('leaves the choice to the person when neither is a membership any more', () => {
    expect(resolveTenant([ACME, BETA], 'ten_gone', 'ten_gone')).toEqual({
      kind: 'choose',
      tenants: [ACME, BETA],
    });
    expect(resolveTenant([ACME, BETA], null, null)).toEqual({
      kind: 'choose',
      tenants: [ACME, BETA],
    });
  });
});

describe('inSelectedTenant', () => {
  const me = session({
    username: 'twohats',
    display_name: 'Two Hats',
    permissions: [],
    tenants: [ACME, BETA],
    tenant_resolved: false,
  });

  it("reads the selected membership's permissions", () => {
    expect(
      inSelectedTenant(me, {
        kind: 'selected',
        tenant: BETA,
        tenants: [ACME, BETA],
      }),
    ).toEqual({
      ...me,
      permissions: ['fleet:view'],
      tenant_resolved: true,
    });
  });

  it("keeps the hub's answer when it resolved the tenant the request named", () => {
    const resolved = session({
      ...me,
      permissions: ['fleet:view', 'user:manage'],
      tenant_resolved: true,
    });
    expect(
      inSelectedTenant(resolved, {
        kind: 'selected',
        tenant: ACME,
        tenants: [ACME, BETA],
      }),
    ).toBe(resolved);
  });

  it("keeps the hub's answer while no membership is selected", () => {
    expect(
      inSelectedTenant(me, { kind: 'choose', tenants: [ACME, BETA] }),
    ).toBe(me);
    expect(inSelectedTenant(me, { kind: 'none' })).toBe(me);
  });
});

describe('hasPermission', () => {
  it('carries only the permissions the session holds, and none without a session', () => {
    expect(hasPermission(session({ permissions: ['user:manage'] }), 'user:manage')).toBe(true);
    expect(hasPermission(session({ permissions: ['fleet:view'] }), 'user:manage')).toBe(false);
    expect(hasPermission(null, 'fleet:view')).toBe(false);
    expect(hasPermission(undefined, 'fleet:view')).toBe(false);
  });
});
