import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectLogoutMutation } from './logout.mutation';

describe('injectLogoutMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => (path === '/api/auth/logout' && method === 'POST' ? {} : {}));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('POSTs /api/auth/logout and drops the cached identity', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectLogoutMutation());

    await mutation.mutateAsync();

    expect(stub.forRoute('/api/auth/logout', 'POST')).toHaveLength(1);
    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'me']);
  });
});
