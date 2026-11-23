import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import {
  QueryClient,
  provideTanStackQuery,
} from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { membership } from './session.builders';
import {
  LAST_TENANT_KEY,
  PAGE_RELOAD,
  TAB_TENANT_KEY,
  TenantContext,
} from './tenant-context';

const ACME = membership({ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view', 'user:manage'] });
const BETA = membership({ tenant_id: 'ten_b', name: 'Beta', permissions: ['fleet:view'] });

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
    expect(tenant.resolve([ACME, BETA])).toMatchObject({
      kind: 'selected',
      tenant: ACME,
    });
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
    expect(tenant.resolve([ACME, BETA])).toMatchObject({
      kind: 'selected',
      tenant: BETA,
    });
  });

  it('re-reads every query but the identity when the entered tenant differs from the one the calls named', () => {
    const tenant = context();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    queryClient.setQueryData(['hub', 'me'], {});
    queryClient.setQueryData(['hub', 'chunks', 'counts'], {});

    tenant.enter('ten_a');

    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(
      queryClient.getQueryState(['hub', 'chunks', 'counts'])?.isInvalidated,
    ).toBe(true);
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
    expect(tenant.resolve([ACME, BETA])).toMatchObject({
      kind: 'selected',
      tenant: BETA,
    });
  });
});
