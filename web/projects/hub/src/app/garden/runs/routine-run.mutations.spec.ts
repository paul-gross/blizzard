import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type RequestClientStub, routineRunResponse, stubError, stubHubClient } from 'fleet/testing';
import { injectRunRoutineMutation } from './routine-run.mutations';

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
    stub = stubHubClient({
      'POST /api/routines/{routine_id}/run': () =>
        routineRunResponse({ chunk_id: 'ch_1', created_at: '2026-01-01T00:00:00Z' }),
    });
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
    stub = stubHubClient({
      'POST /api/routines/{routine_id}/run': () => stubError(503, { detail: "scope 'cold' is retired" }),
    });
    const mutation = TestBed.runInInjectionContext(() => injectRunRoutineMutation());

    await expect(
      mutation.mutateAsync({ routineId: 'rtn_1', scopeSlug: 'cold', mode: 'full', note: null }),
    ).rejects.toBeTruthy();
  });
});
