import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectCreateScopeMutation } from './scopes.mutations';

describe('injectCreateScopeMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('POSTs /api/scopes with the slug and description and invalidates the scope picker', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      method === 'POST' && path === '/api/scopes'
        ? { slug: 'new-scope', description: 'a fresh weed patch', created_at: '2026-01-01T00:00:00Z', retired: false }
        : {},
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateScopeMutation());

    const result = await mutation.mutateAsync({ slug: 'new-scope', description: 'a fresh weed patch' });

    const requests = stub.forRoute('/api/scopes', 'POST');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ slug: 'new-scope', description: 'a fresh weed patch' });
    expect(result.slug).toBe('new-scope');
    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'scopes']);
  });

  it('surfaces a refused scope create (422) as the mutation error', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      method === 'POST' && path === '/api/scopes' ? stubError(422, { detail: 'malformed slug' }) : {},
    );
    const mutation = TestBed.runInInjectionContext(() => injectCreateScopeMutation());

    await expect(mutation.mutateAsync({ slug: '!!!', description: '' })).rejects.toBeTruthy();
  });
});
