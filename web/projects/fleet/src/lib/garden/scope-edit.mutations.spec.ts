import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectEditScopeMutation } from './scope-edit.mutations';

describe('injectEditScopeMutation (blizzard#400)', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({ slug: 'blizzard', description: 'updated', retired: false }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('patches the description onto the scope', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectEditScopeMutation());

    await mutation.mutateAsync({ slug: 'blizzard', description: 'updated' });

    const calls = stub.forRoute('/api/scopes/blizzard', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ description: 'updated' });
  });

  it('invalidates the scope list on a successful edit', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectEditScopeMutation());

    await mutation.mutateAsync({ slug: 'blizzard', description: 'updated' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'scopes']);
  });
});
