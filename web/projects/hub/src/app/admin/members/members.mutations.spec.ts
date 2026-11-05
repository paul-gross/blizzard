import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { memberView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { injectMemberRoleMutation, injectRevokeMemberMutation } from './members.mutations';

const GRACE = memberView({
  user_id: 'usr_2',
  username: 'grace',
  display_name: 'Grace',
  email: null,
  role: 'contributor',
  hub_admin: false,
  created_at: '2026-07-21T00:00:00Z',
});

describe('membership mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  function invalidatedKeys(spy: { mock: { calls: readonly (readonly unknown[])[] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
  }

  it('PUTs /api/members/{id} with the role and re-reads the listing', async () => {
    stub = stubHubClient({ 'PUT /api/members/{user_id}': () => GRACE });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectMemberRoleMutation());

    const result = await mutation.mutateAsync({ userId: 'usr_2', role: 'contributor' });

    const requests = stub.forRoute('/api/members/usr_2', 'PUT');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ role: 'contributor' });
    expect(result.role).toBe('contributor');
    expect(invalidatedKeys(invalidateSpy)).toContainEqual(['hub', 'members']);
  });

  it('rejects a refused role change (409, the last admin)', async () => {
    stub = stubHubClient({
      'PUT /api/members/{user_id}': () => stubError(409, { detail: 'a tenant keeps at least one admin' }),
    });
    const mutation = TestBed.runInInjectionContext(() => injectMemberRoleMutation());

    await expect(mutation.mutateAsync({ userId: 'usr_2', role: 'guest' })).rejects.toBeTruthy();
  });

  it('DELETEs /api/members/{id} and re-reads the listing', async () => {
    stub = stubHubClient({ 'DELETE /api/members/{user_id}': () => undefined });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectRevokeMemberMutation());

    await mutation.mutateAsync({ userId: 'usr_2' });

    expect(stub.forRoute('/api/members/usr_2', 'DELETE')).toHaveLength(1);
    expect(invalidatedKeys(invalidateSpy)).toContainEqual(['hub', 'members']);
  });

  it('rejects a refused revocation (403, your own membership)', async () => {
    stub = stubHubClient({
      'DELETE /api/members/{user_id}': () => stubError(403, { detail: 'cannot change your own role' }),
    });
    const mutation = TestBed.runInInjectionContext(() => injectRevokeMemberMutation());

    await expect(mutation.mutateAsync({ userId: 'usr_2' })).rejects.toBeTruthy();
  });
});
