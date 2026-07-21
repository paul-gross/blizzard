import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectAssignRoleMutation } from './assign-role.mutation';

describe('injectAssignRoleMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('POSTs /api/users/{id}/role with the target role and invalidates the listing', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/users/usr_2/role' && method === 'POST'
        ? {
            user_id: 'usr_2',
            username: 'grace',
            display_name: 'Grace',
            email: null,
            role: 'contributor',
            created_at: '2026-07-21T00:00:00Z',
          }
        : {},
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectAssignRoleMutation());

    const result = await mutation.mutateAsync({ userId: 'usr_2', role: 'contributor' });

    const requests = stub.forRoute('/api/users/usr_2/role', 'POST');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ role: 'contributor' });
    expect(result.role).toBe('contributor');
    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'users']);
  });

  it('surfaces a refused role change (403) as the mutation error', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/users/usr_2/role' ? stubError(403, { detail: 'cannot change your own role' }) : {},
    );
    const mutation = TestBed.runInInjectionContext(() => injectAssignRoleMutation());

    await expect(mutation.mutateAsync({ userId: 'usr_2', role: 'admin' })).rejects.toBeTruthy();
  });
});
