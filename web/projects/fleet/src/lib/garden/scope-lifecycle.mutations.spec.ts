import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectScopeLifecycleMutation } from './scope-lifecycle.mutations';

describe('injectScopeLifecycleMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({}));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('routes to the retire verb when retired=true, with `by: operator`', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectScopeLifecycleMutation());

    await mutation.mutateAsync({ slug: 'blizzard', retired: true });

    const calls = stub.forRoute('/api/scopes/blizzard/retire', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/scopes/blizzard/enable', 'POST')).toHaveLength(0);
  });

  it('routes to the enable verb when retired=false', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectScopeLifecycleMutation());

    await mutation.mutateAsync({ slug: 'blizzard', retired: false });

    const calls = stub.forRoute('/api/scopes/blizzard/enable', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/scopes/blizzard/retire', 'POST')).toHaveLength(0);
  });

  it('invalidates the scope list on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectScopeLifecycleMutation());

    await mutation.mutateAsync({ slug: 'blizzard', retired: true });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'scopes']);
  });
});
