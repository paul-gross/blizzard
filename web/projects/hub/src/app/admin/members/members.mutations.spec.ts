import { memberWire } from './members-fixture.translate';
import { member } from './members.builders';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { injectMemberRoleMutation, injectRevokeMemberMutation } from './members.mutations';

const GRACE = member({
  userId: 'usr_2',
  username: 'grace',
  displayName: 'Grace',
  email: null,
  role: 'contributor',
  hubAdmin: false,
  createdAt: '2026-07-21T00:00:00Z',
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
    stub = stubHubClient({ 'PUT /api/members/{user_id}': () => memberWire(GRACE) });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectMemberRoleMutation());

    const result = await mutation.mutateAsync({ userId: 'usr_2', role: 'contributor' });

    const requests = stub.forRoute('/api/members/usr_2', 'PUT');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ role: 'contributor' });
    expect(result.role).toBe('contributor');
    expect(invalidatedKeys(invalidateSpy)).toContainEqual(['hub', 'members']);
  });

  it('waits for the listing refresh before settling either mutation', async () => {
    stub = stubHubClient({
      'PUT /api/members/{user_id}': () => memberWire(GRACE),
      'DELETE /api/members/{user_id}': () => undefined,
    });
    let finish!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    const role = TestBed.runInInjectionContext(() => injectMemberRoleMutation());
    let settled = false;
    const changing = role.mutateAsync({ userId: 'usr_2', role: 'guest' }).then(() => { settled = true; });
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    expect(settled).toBe(false);
    finish();
    await changing;
    expect(settled).toBe(true);

    const revoke = TestBed.runInInjectionContext(() => injectRevokeMemberMutation());
    settled = false;
    finish = undefined!;
    const revoking = revoke.mutateAsync({ userId: 'usr_2' }).then(() => { settled = true; });
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    expect(settled).toBe(false);
    finish();
    await revoking;
    expect(settled).toBe(true);
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

  it.each(['role', 'revoke'] as const)('awaits listing invalidation before settling a refused %s mutation', async (kind) => {
    stub = stubHubClient({
      'PUT /api/members/{user_id}': () => stubError(409, { detail: 'refused role' }),
      'DELETE /api/members/{user_id}': () => stubError(403, { detail: 'refused revoke' }),
    });
    let finish!: () => void;
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(
      () => new Promise<void>((resolve) => { finish = resolve; }),
    );
    const role = TestBed.runInInjectionContext(() => injectMemberRoleMutation());
    const revoke = TestBed.runInInjectionContext(() => injectRevokeMemberMutation());
    let settled = false;
    const request = kind === 'role'
      ? role.mutateAsync({ userId: 'usr_2', role: 'guest' })
      : revoke.mutateAsync({ userId: 'usr_2' });
    const outcome = request.then(
      () => { settled = true; return null; },
      (error: unknown) => { settled = true; return error; },
    );
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    expect(invalidatedKeys(invalidate)).toContainEqual(['hub', 'members']);
    expect(settled).toBe(false);
    finish();
    expect(await outcome).toMatchObject({ detail: kind === 'role' ? 'refused role' : 'refused revoke' });
    expect(settled).toBe(true);
  });
});
