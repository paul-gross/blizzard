import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenCadenceView, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectCadenceLifecycleMutation } from './cadence-lifecycle.mutations';

describe('injectCadenceLifecycleMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'POST /api/cadences/{cadence_id}/retire': () => gardenCadenceView({ slug: 'evening', retired: true }),
      'POST /api/cadences/{cadence_id}/enable': () => gardenCadenceView({ slug: 'evening' }),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
  });

  afterEach(() => stub.restore());

  it('retires against the read revision, by the operator', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectCadenceLifecycleMutation());

    await mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 2, retired: true });

    const calls = stub.forRoute('/api/cadences/cad_evening/retire', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('2');
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/cadences/cad_evening/enable', 'POST')).toHaveLength(0);
  });

  it('enables against the read revision', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectCadenceLifecycleMutation());

    await mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 5, retired: false });

    const calls = stub.forRoute('/api/cadences/cad_evening/enable', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('5');
  });

  it('invalidates the cadence list and its errands', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCadenceLifecycleMutation());

    await mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 1, retired: true });

    const keys = spy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toEqual([
      ['hub', 'cadences'],
      ['hub', 'cadence-errands', 'cad_evening'],
    ]);
  });
});
