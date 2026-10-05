import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubClient, hubConfigKey } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import {
  injectCreateRepositoryMutation,
  injectEditRepositoryMutation,
  injectRepositoryLifecycleMutation,
} from './repositories.mutations';

describe('repository mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({}));
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: unknown }).queryKey);
  }

  it('edits with the shown revision as If-Match, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectEditRepositoryMutation());

    await mutation.mutateAsync({
      name: 'a',
      revision: 3,
      body: { owner: null },
    });

    const calls = stub.forRoute('/api/repositories/a', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ owner: null });
    expect(calls[0].headers['if-match']).toBe('3');
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });

  it('routes retire and enable by the desired state, each sending If-Match', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectRepositoryLifecycleMutation());

    await mutation.mutateAsync({ name: 'a', revision: 2, retired: true });
    await mutation.mutateAsync({ name: 'a', revision: 3, retired: false });

    expect(stub.forRoute('/api/repositories/a/retire', 'POST')[0].headers['if-match']).toBe('2');
    expect(stub.forRoute('/api/repositories/a/enable', 'POST')[0].headers['if-match']).toBe('3');
  });

  it('creates, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateRepositoryMutation());

    await mutation.mutateAsync({
      name: 'a',
      owner: 'o',
      repo: 'r',
      base_branch: 'main',
      forge_api_url: 'https://x',
      secret_name: 's',
    });

    expect(stub.forRoute('/api/repositories', 'POST')).toHaveLength(1);
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });
});
