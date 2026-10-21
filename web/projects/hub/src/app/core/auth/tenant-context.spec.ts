import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import type { MeResponse, MeTenant } from 'fleet/shell';
import {
  LAST_TENANT_KEY,
  PAGE_RELOAD,
  TAB_TENANT_KEY,
  TenantContext,
  inSelectedTenant,
  resolveTenant,
} from './tenant-context';

const ACME: MeTenant = { tenant_id: 'ten_a', name: 'Acme', role: 'admin', permissions: ['fleet:view', 'user:manage'] };
const BETA: MeTenant = { tenant_id: 'ten_b', name: 'Beta', role: 'guest', permissions: ['fleet:view'] };

describe('resolveTenant', () => {
  it('has nothing to enter without a membership', () => {
    expect(resolveTenant([], 'ten_a', 'ten_a')).toEqual({ kind: 'none' });
  });

  it('selects the only membership whatever the tab or the browser remembered', () => {
    expect(resolveTenant([ACME], null, null)).toEqual({ kind: 'selected', tenant: ACME, tenants: [ACME] });
    expect(resolveTenant([ACME], 'ten_gone', 'ten_gone')).toEqual({ kind: 'selected', tenant: ACME, tenants: [ACME] });
  });

  it("keeps the tab's tenant while it is still a membership, ahead of the browser's last choice", () => {
    expect(resolveTenant([ACME, BETA], 'ten_b', 'ten_a')).toMatchObject({ kind: 'selected', tenant: BETA });
  });

  it("falls back to the browser's last choice while it is still a membership", () => {
    expect(resolveTenant([ACME, BETA], 'ten_gone', 'ten_a')).toMatchObject({ kind: 'selected', tenant: ACME });
    expect(resolveTenant([ACME, BETA], null, 'ten_b')).toMatchObject({ kind: 'selected', tenant: BETA });
  });

  it('leaves the choice to the person when neither is a membership any more', () => {
    expect(resolveTenant([ACME, BETA], 'ten_gone', 'ten_gone')).toEqual({ kind: 'choose', tenants: [ACME, BETA] });
    expect(resolveTenant([ACME, BETA], null, null)).toEqual({ kind: 'choose', tenants: [ACME, BETA] });
  });
});

describe('inSelectedTenant', () => {
  const me: MeResponse = {
    user_id: 'usr_1',
    username: 'twohats',
    display_name: 'Two Hats',
    role: null,
    permissions: [],
    tenants: [ACME, BETA],
    hub_admin: false,
    assignable_roles: [],
  };

  it("reads the selected membership's role and permissions", () => {
    expect(inSelectedTenant(me, { kind: 'selected', tenant: BETA, tenants: [ACME, BETA] })).toEqual({
      ...me,
      role: 'guest',
      permissions: ['fleet:view'],
    });
  });

  it("keeps the hub's answer when it resolved the tenant the request named", () => {
    const resolved: MeResponse = { ...me, role: 'admin', permissions: ['fleet:view', 'user:manage'] };
    expect(inSelectedTenant(resolved, { kind: 'selected', tenant: ACME, tenants: [ACME, BETA] })).toBe(resolved);
  });

  it("keeps the hub's answer while no membership is selected", () => {
    expect(inSelectedTenant(me, { kind: 'choose', tenants: [ACME, BETA] })).toBe(me);
    expect(inSelectedTenant(me, { kind: 'none' })).toBe(me);
  });
});

describe('TenantContext', () => {
  let queryClient: QueryClient;
  let reload: ReturnType<typeof vi.fn>;

  function context(): TenantContext {
    queryClient = new QueryClient();
    reload = vi.fn();
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: PAGE_RELOAD, useValue: reload },
      ],
    });
    return TestBed.inject(TenantContext);
  }

  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("opens on this tab's tenant", () => {
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_b');
    localStorage.setItem(LAST_TENANT_KEY, 'ten_a');

    expect(context().current()).toBe('ten_b');
  });

  it("opens a new tab on the browser's last choice", () => {
    localStorage.setItem(LAST_TENANT_KEY, 'ten_a');

    const tenant = context();

    expect(tenant.current()).toBe('ten_a');
    expect(tenant.resolve([ACME, BETA])).toMatchObject({ kind: 'selected', tenant: ACME });
  });

  it('knows no tenant in a fresh browser', () => {
    expect(context().current()).toBeNull();
  });

  it('holds the entered tenant for this tab and remembers it as the last choice', () => {
    const tenant = context();

    tenant.enter('ten_b');

    expect(tenant.current()).toBe('ten_b');
    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_b');
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_b');
    expect(tenant.resolve([ACME, BETA])).toMatchObject({ kind: 'selected', tenant: BETA });
  });

  it('re-reads every query but the identity when the entered tenant differs from the one the calls named', () => {
    const tenant = context();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    queryClient.setQueryData(['hub', 'me'], {});
    queryClient.setQueryData(['hub', 'chunks', 'counts'], {});

    tenant.enter('ten_a');

    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryState(['hub', 'chunks', 'counts'])?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(['hub', 'me'])?.isInvalidated).toBe(false);

    tenant.enter('ten_a');
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('switches by remembering the tenant and reloading the page into it', () => {
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_a');
    const tenant = context();

    tenant.switchTo('ten_b');

    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_b');
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_b');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('holds the selection in memory when the browser refuses storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    const tenant = context();
    expect(tenant.current()).toBeNull();

    tenant.enter('ten_b');

    expect(tenant.current()).toBe('ten_b');
    expect(tenant.resolve([ACME, BETA])).toMatchObject({ kind: 'selected', tenant: BETA });
  });
});
