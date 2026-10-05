import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubClient, hubConfigKey } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import {
  injectCreateSecretMutation,
  injectReplaceSecretMutation,
  injectSecretLifecycleMutation,
} from './secrets.mutations';

describe('secret mutations', () => {
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

  it('replaces the value with the shown revision as If-Match, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectReplaceSecretMutation());

    await mutation.mutateAsync({ name: 'tok', revision: 4, value: 'hunter2' });

    const calls = stub.forRoute('/api/secrets/tok/value', 'PUT');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ value: 'hunter2' });
    expect(calls[0].headers['if-match']).toBe('4');
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });

  it('retires and enables without If-Match', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectSecretLifecycleMutation());

    await mutation.mutateAsync({ name: 'tok', retired: true });
    await mutation.mutateAsync({ name: 'tok', retired: false });

    expect(stub.forRoute('/api/secrets/tok/retire', 'POST')[0].headers['if-match']).toBeUndefined();
    expect(stub.forRoute('/api/secrets/tok/enable', 'POST')).toHaveLength(1);
  });

  it('creates with the value in the request body, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateSecretMutation());

    await mutation.mutateAsync({ name: 'tok', value: 'hunter2' });

    expect(stub.forRoute('/api/secrets', 'POST')[0].body).toEqual({
      name: 'tok',
      value: 'hunter2',
    });
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });
});
