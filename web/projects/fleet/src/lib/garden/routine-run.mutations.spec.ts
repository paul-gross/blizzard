import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectCreateScopeMutation, injectRunRoutineMutation } from './routine-run.mutations';

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

describe('injectRunRoutineMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('POSTs /api/routines/{routine_id}/run with the scope, mode, and note, and invalidates the routine list', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      method === 'POST' && path === '/api/routines/rtn_1/run'
        ? {
            chunk_id: 'ch_1',
            source: 'hub',
            ref: '1',
            title: 'gardening run (full)',
            body: 'Routine: gardening',
            routine_name: 'gardening',
            scope_slug: 'blizzard',
            effective_mode: 'full',
            downgraded: false,
            baseline_finding_set_id: null,
            baseline_revisions: null,
            created_at: '2026-01-01T00:00:00Z',
          }
        : {},
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectRunRoutineMutation());

    const result = await mutation.mutateAsync({ routineId: 'rtn_1', scopeSlug: 'blizzard', mode: 'full', note: null });

    const requests = stub.forRoute('/api/routines/rtn_1/run', 'POST');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ scope_slug: 'blizzard', mode: 'full', note: null });
    expect(result.chunk_id).toBe('ch_1');
    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'routines']);
  });

  it('surfaces a refused run (503, a retired scope) as the mutation error', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      method === 'POST' && path === '/api/routines/rtn_1/run' ? stubError(503, { detail: "scope 'cold' is retired" }) : {},
    );
    const mutation = TestBed.runInInjectionContext(() => injectRunRoutineMutation());

    await expect(
      mutation.mutateAsync({ routineId: 'rtn_1', scopeSlug: 'cold', mode: 'full', note: null }),
    ).rejects.toBeTruthy();
  });
});
