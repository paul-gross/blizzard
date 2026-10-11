import { describe, expect, it } from 'vitest';

import type { MeResponse, MeTenant } from 'fleet/shell';

import { resolveTenant } from '../core/auth/tenant-context';

import { authState, connectionLabel, logoutErrorText, selectedTenantName, tenancyOf } from './app.model';

const ACME: MeTenant = { tenant_id: 'ten_a', name: 'Acme', role: 'admin', permissions: ['fleet:view'] };

describe('authState', () => {
  it('is loading while the identity read is pending', () => {
    expect(authState(true, null)).toBe('loading');
    expect(authState(true, { kind: 'selected', tenant: ACME, tenants: [ACME] })).toBe('loading');
  });

  it('is unauthenticated without an identity', () => {
    expect(authState(false, null)).toBe('unauthenticated');
  });

  it('is no-access for an identity holding no membership', () => {
    expect(authState(false, { kind: 'none' })).toBe('no-access');
  });

  it('is choose while several memberships leave the tenant to the person', () => {
    expect(authState(false, { kind: 'choose', tenants: [ACME] })).toBe('choose');
  });

  it('is ready once a tenant is selected', () => {
    expect(authState(false, { kind: 'selected', tenant: ACME, tenants: [ACME] })).toBe('ready');
  });
});

describe('tenancyOf', () => {
  const resolve = (tenants: readonly MeTenant[]) => resolveTenant(tenants, null, null);
  const me: MeResponse = { user_id: 'u', username: 'u', display_name: 'u', role: null, permissions: [], tenants: [ACME] };

  it('is null without an identity', () => {
    expect(tenancyOf(null, resolve)).toBeNull();
  });

  it("settles the identity's memberships", () => {
    expect(tenancyOf(me, resolve)).toEqual({ kind: 'selected', tenant: ACME, tenants: [ACME] });
  });

  it('reads an identity listing no memberships as holding none', () => {
    expect(tenancyOf({ ...me, tenants: undefined }, resolve)).toEqual({ kind: 'none' });
  });
});

describe('selectedTenantName', () => {
  it("is the selected tenant's name, and empty while none is selected", () => {
    expect(selectedTenantName({ kind: 'selected', tenant: ACME, tenants: [ACME] })).toBe('Acme');
    expect(selectedTenantName({ kind: 'choose', tenants: [ACME] })).toBe('');
    expect(selectedTenantName(null)).toBe('');
  });
});

describe('connectionLabel', () => {
  it('reports a reconnecting stream ahead of the health read', () => {
    expect(connectionLabel('reconnecting', true, true, 'ok')).toBe('reconnecting…');
  });

  it('is connecting while the health read is pending', () => {
    expect(connectionLabel('idle', true, false, undefined)).toBe('connecting…');
  });

  it('is offline once the health read fails', () => {
    expect(connectionLabel('closed', false, true, undefined)).toBe('offline');
  });

  it("reports the health read's own status, defaulting to ok", () => {
    expect(connectionLabel('open', false, false, 'degraded')).toBe('degraded');
    expect(connectionLabel('open', false, false, undefined)).toBe('ok');
  });
});

describe('logoutErrorText', () => {
  it('is null without an error', () => {
    expect(logoutErrorText(null)).toBeNull();
    expect(logoutErrorText(undefined)).toBeNull();
  });

  it("reads the failure's own detail through errorMessage, falling back to a generic line", () => {
    expect(logoutErrorText({ detail: 'session store down' })).toBe('session store down');
    expect(logoutErrorText(new Error('network'))).toBe('Log out failed');
  });
});
