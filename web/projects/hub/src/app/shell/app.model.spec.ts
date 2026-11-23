import { describe, expect, it } from 'vitest';

import { membership, session } from '../core/auth/session.builders';
import { type Membership, resolveTenant } from '../core/auth/session.domain';

import {
  authState,
  connectionLabel,
  logoutErrorText,
  selectedTenantName,
  tenancyOf,
  shellUrl,
  shellView,
} from './app.model';

const ACME = membership({ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view'] });

describe('shell loading route', () => {
  it('prefers the navigation in flight, then the completed route, then the initial address', () => {
    expect(shellUrl('/events/7', false, '/', '/board')).toBe('/events/7');
    expect(shellUrl(null, true, '/fleet', '/board')).toBe('/fleet');
    expect(shellUrl(null, false, '/', '/login')).toBe('/login');
  });

  it('finds the active view through a project lens and detail path', () => {
    expect(shellView('/p/blizzard/events/-1?at=now')).toBe('events');
    expect(shellView('/board/chunk/one')).toBe('board');
    expect(shellView('/gardening/proposals')).toBe('gardening');
  });
});

describe('authState', () => {
  it('is loading while the identity read is pending', () => {
    expect(authState(true, null)).toBe('loading');
    expect(
      authState(true, { kind: 'selected', tenant: ACME, tenants: [ACME] }),
    ).toBe('loading');
  });

  it('is unauthenticated without an identity', () => {
    expect(authState(false, null)).toBe('unauthenticated');
  });

  it('is no-access for an identity holding no membership', () => {
    expect(authState(false, { kind: 'none' })).toBe('no-access');
  });

  it('is choose while several memberships leave the tenant to the person', () => {
    expect(authState(false, { kind: 'choose', tenants: [ACME] })).toBe(
      'choose',
    );
  });

  it('is ready once a tenant is selected', () => {
    expect(
      authState(false, { kind: 'selected', tenant: ACME, tenants: [ACME] }),
    ).toBe('ready');
  });
});

describe('tenancyOf', () => {
  const resolve = (tenants: readonly Membership[]) =>
    resolveTenant(tenants, null, null);
  const me = session({ permissions: [], tenants: [ACME], tenant_resolved: false });

  it('is null without an identity', () => {
    expect(tenancyOf(null, resolve)).toBeNull();
  });

  it("settles the identity's memberships", () => {
    expect(tenancyOf(me, resolve)).toEqual({
      kind: 'selected',
      tenant: ACME,
      tenants: [ACME],
    });
  });

  it('reads an identity listing no memberships as holding none', () => {
    expect(tenancyOf({ ...me, tenants: [] }, resolve)).toEqual({
      kind: 'none',
    });
  });
});

describe('selectedTenantName', () => {
  it("is the selected tenant's name, and empty while none is selected", () => {
    expect(
      selectedTenantName({ kind: 'selected', tenant: ACME, tenants: [ACME] }),
    ).toBe('Acme');
    expect(selectedTenantName({ kind: 'choose', tenants: [ACME] })).toBe('');
    expect(selectedTenantName(null)).toBe('');
  });
});

describe('connectionLabel', () => {
  it('reports a reconnecting stream ahead of the health read', () => {
    expect(connectionLabel('reconnecting', true, true, 'ok')).toBe(
      'reconnecting…',
    );
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
    expect(logoutErrorText({ detail: 'session store down' })).toBe(
      'session store down',
    );
    expect(logoutErrorText(new Error('network'))).toBe('Log out failed');
  });
});
