import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenRoutineView, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectEditRoutineMutation } from './routine-edit.mutations';

describe('injectEditRoutineMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'PATCH /api/routines/{routine_id}': () =>
        gardenRoutineView({ routine_id: 'rt_1', name: 'nightly', graph_name: 'garden-delta' }),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('patches the graph onto the routine', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectEditRoutineMutation());

    await mutation.mutateAsync({ routineId: 'rt_1', graphName: 'garden-delta' });

    const calls = stub.forRoute('/api/routines/rt_1', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ graph_name: 'garden-delta' });
  });

  it('sets or clears the routine default without changing the graph', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectEditRoutineMutation());
    await mutation.mutateAsync({ routineId: 'rt_1', defaultUrgency: 'urgent' });
    await mutation.mutateAsync({ routineId: 'rt_1', defaultUrgency: null });
    const calls = stub.forRoute('/api/routines/rt_1', 'PATCH');
    expect(calls[0].body).toEqual({ default_urgency: 'urgent' });
    expect(calls[1].body).toEqual({ default_urgency: null });
  });

  it('invalidates the routine list on a successful edit', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectEditRoutineMutation());

    await mutation.mutateAsync({ routineId: 'rt_1', graphName: 'garden-delta' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'routines']);
  });
});
