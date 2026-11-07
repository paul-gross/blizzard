import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import {
  QueryClient,
  provideTanStackQuery,
} from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectLogoutMutation } from './logout.mutations';

describe('injectLogoutMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({ 'POST /api/auth/logout': () => undefined });
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
      ],
    });
  });

  afterEach(() => stub.restore());

  it('POSTs /api/auth/logout and drops the cached identity', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() =>
      injectLogoutMutation(),
    );

    await mutation.mutateAsync();

    expect(stub.forRoute('/api/auth/logout', 'POST')).toHaveLength(1);
    const keys = invalidateSpy.mock.calls.map(
      (call) => (call[0] as { queryKey: readonly unknown[] }).queryKey,
    );
    expect(keys).toContainEqual(['hub', 'me']);
  });
});
