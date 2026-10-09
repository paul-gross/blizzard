import { Injector, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { EVENT_SOURCE_FACTORY, hubClient, meApiMeGet } from 'fleet';
import { TAB_TENANT_KEY, TENANT_HEADER, TenantContext } from './tenant-context';
import { provideTenantInterceptor } from './tenant.interceptor';

describe('provideTenantInterceptor', () => {
  let seen: (string | null)[];

  beforeEach(() => {
    seen = [];
    const fakeFetch = async (input: Request): Promise<Response> => {
      seen.push(input.headers.get(TENANT_HEADER));
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        provideTenantInterceptor(),
      ],
    });
  });

  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('names the tenant the board entered on every hub call', async () => {
    TestBed.inject(Injector); // runs the environment initializers, which register the interceptor
    TestBed.inject(TenantContext).enter('ten_a');
    await meApiMeGet({ throwOnError: false });

    TestBed.inject(TenantContext).enter('ten_b');
    await meApiMeGet({ throwOnError: false });

    expect(seen.slice(-2)).toEqual(['ten_a', 'ten_b']);
  });

  it("names this tab's stored tenant from the first call", async () => {
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_b');
    TestBed.inject(Injector);

    await meApiMeGet({ throwOnError: false });

    expect(seen.at(-1)).toBe('ten_b');
  });

  it('sends no tenant header while the board knows no tenant', async () => {
    TestBed.inject(Injector);

    await meApiMeGet({ throwOnError: false });

    expect(seen.at(-1)).toBeNull();
  });

  it('opens the live event stream with the same tenant header', async () => {
    const streamHeaders: Headers[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        streamHeaders.push(new Headers(init.headers));
        return new Response(null, { status: 401 });
      }),
    );
    TestBed.inject(TenantContext).enter('ten_a');

    const source = TestBed.inject(EVENT_SOURCE_FACTORY)('/api/events/stream');
    await vi.waitFor(() => expect(streamHeaders).toHaveLength(1));
    source.close();

    expect(streamHeaders[0].get(TENANT_HEADER)).toBe('ten_a');
    expect(streamHeaders[0].get('accept')).toBe('text/event-stream');
  });
});
