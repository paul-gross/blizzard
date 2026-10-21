import { Injector, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { EVENT_SOURCE_FACTORY, FLEET_CLOCK, chunkCountsApiChunkCountsGet, hubClient, type MeResponse } from 'fleet';
import type { MeTenant } from 'fleet/shell';
import { type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { LAST_TENANT_KEY, PAGE_RELOAD, TAB_TENANT_KEY, TenantContext } from './tenant-context';
import { TENANT_CONFIRMED_MS } from './tenant-loss';
import { provideTenantInterceptor } from './tenant.interceptor';

const ACME: MeTenant = { tenant_id: 'ten_a', name: 'Acme', role: 'admin', permissions: ['fleet:view'] };
const BETA: MeTenant = { tenant_id: 'ten_b', name: 'Beta', role: 'guest', permissions: ['fleet:view'] };
const GLOBEX: MeTenant = { tenant_id: 'ten_g', name: 'Globex', role: 'guest', permissions: ['fleet:view'] };

const me = (tenants: readonly MeTenant[]): MeResponse => ({
  user_id: 'usr_1',
  username: 'two',
  display_name: 'Two',
  role: null,
  permissions: [],
  tenants: [...tenants],
  hub_admin: false,
  assignable_roles: [],
});

/** Lets every pending 404 report and `/api/me` read run to its end — a few macrotask turns. */
async function drain(): Promise<void> {
  for (let turn = 0; turn < 5; turn++) await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('TenantLoss — a board that loses its tenant', () => {
  let memberships: readonly MeTenant[];
  let stub: RequestClientStub;
  let reload: ReturnType<typeof vi.fn>;
  let nowMs: number;

  /** The resolution a fresh start of the page makes from what the stores remember now. */
  const atStartup = (tenants: readonly MeTenant[]) =>
    TestBed.runInInjectionContext(() => new TenantContext()).resolve(tenants);

  /** Opens the board in `tenant`, remembered by this tab and the browser, as a tab that settled on it does. */
  const openIn = (tenant: string) => {
    sessionStorage.setItem(TAB_TENANT_KEY, tenant);
    localStorage.setItem(LAST_TENANT_KEY, tenant);
    TestBed.inject(Injector); // registers the interceptors
  };

  const meReads = () => stub.forRoute('/api/me').length;

  beforeEach(() => {
    memberships = [ACME, GLOBEX];
    // Every tenant-scoped read answers 404 — the tenant is gone, or the record is.
    stub = stubRequestClient(hubClient, (_method, path) =>
      path === '/api/me' ? me(memberships) : stubError(404, { detail: 'not found' }),
    );
    reload = vi.fn();
    nowMs = Date.parse('2026-10-09T12:00:00Z');
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        provideTenantInterceptor(),
        { provide: PAGE_RELOAD, useValue: reload },
        { provide: FLEET_CLOCK, useValue: () => nowMs },
      ],
    });
  });

  afterEach(() => {
    stub.restore();
    sessionStorage.clear();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('leaves a deleted tenant for the only membership left', async () => {
    openIn('ten_g');
    memberships = [ACME];

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));

    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBeNull();
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBeNull();
    expect(atStartup(memberships)).toEqual({ kind: 'selected', tenant: ACME, tenants: memberships });
  });

  it('leaves a deleted tenant for the picker when several memberships are left', async () => {
    memberships = [ACME, BETA, GLOBEX];
    openIn('ten_g');
    memberships = [ACME, BETA];

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));

    expect(atStartup(memberships)).toEqual({ kind: 'choose', tenants: memberships });
  });

  it('leaves a deleted tenant for the no-access screen when no membership is left', async () => {
    memberships = [GLOBEX];
    openIn('ten_g');
    memberships = [];

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));

    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBeNull();
    expect(localStorage.getItem(LAST_TENANT_KEY)).toBeNull();
    expect(atStartup(memberships)).toEqual({ kind: 'none' });
  });

  it("keeps a browser's last choice another tab made since, when it is still a membership", async () => {
    memberships = [ACME, BETA, GLOBEX];
    openIn('ten_g');
    localStorage.setItem(LAST_TENANT_KEY, 'ten_b');
    memberships = [ACME, BETA];

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));

    expect(localStorage.getItem(LAST_TENANT_KEY)).toBe('ten_b');
    expect(atStartup(memberships)).toEqual({ kind: 'selected', tenant: BETA, tenants: memberships });
  });

  it('changes nothing for a missing record in a live tenant, and does not re-read for every such 404', async () => {
    openIn('ten_g');

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(meReads()).toBe(1));
    await drain();
    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await drain();

    expect(meReads()).toBe(1);
    expect(reload).not.toHaveBeenCalled();
    expect(TestBed.inject(TenantContext).current()).toBe('ten_g');
    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_g');

    // Once the confirmation has aged, a 404 asks again — the tenant may have gone since.
    nowMs += TENANT_CONFIRMED_MS;
    memberships = [ACME];
    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(meReads()).toBe(2);
  });

  it('reads /api/me once however many 404s arrive together', async () => {
    openIn('ten_g');
    memberships = [ACME];

    await Promise.all([1, 2, 3, 4].map(() => chunkCountsApiChunkCountsGet({ throwOnError: false })));
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    await drain();

    expect(meReads()).toBe(1);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('asks nothing for a 404 to a call that named no tenant', async () => {
    TestBed.inject(Injector);

    await chunkCountsApiChunkCountsGet({ throwOnError: false });
    await drain();

    expect(meReads()).toBe(0);
  });

  it('leaves the tenant when the live event stream answers 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 404 })));
    openIn('ten_g');
    memberships = [ACME];

    const source = TestBed.inject(EVENT_SOURCE_FACTORY)('/api/events/stream');
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    source.close();

    expect(meReads()).toBe(1);
    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBeNull();
  });
});
