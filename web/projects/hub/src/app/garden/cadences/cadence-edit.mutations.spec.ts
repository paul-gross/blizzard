import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { HUB_PROJECT_FILTER } from 'fleet/shell';
import { gardenCadenceView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { injectCreateCadenceMutation, injectEditCadenceMutation } from './cadence-edit.mutations';

describe('cadence edit mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    });
  }

  afterEach(() => stub.restore());

  function invalidated(spy: { mock: { calls: unknown[][] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
  }

  it('creates a cadence in the lensed project and answers it', async () => {
    configure({ 'POST /api/projects/{project}/cadences': () => gardenCadenceView({ slug: 'evening' }) });
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateCadenceMutation());

    const created = await mutation.mutateAsync({ slug: 'evening', spacing: '1d' });

    expect(created.slug).toBe('evening');
    const calls = stub.forRoute('/api/projects/acme/cadences', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ slug: 'evening', spacing: '1d' });
    expect(invalidated(spy)).toContainEqual(['hub', 'cadences']);
  });

  it('patches the whole chore list against the read revision', async () => {
    configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening', revision: 4 }) });
    const mutation = TestBed.runInInjectionContext(() => injectEditCadenceMutation());
    const chores = [{ key: 'nightly', routine: 'nightly', scopes: 'all' as const, mode: 'full' as const, promote: true, note: null }];

    await mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 3, body: { chores } });

    const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ chores });
  });

  it('settles only once the cadence list and its errands have refetched', async () => {
    configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening' }) });
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const mutation = TestBed.runInInjectionContext(() => injectEditCadenceMutation());

    let done = false;
    const settled = mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 1, body: { name: 'e' } }).then(() => {
      done = true;
    });
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await settled;

    expect(invalidated(spy)).toEqual([
      ['hub', 'cadences'],
      ['hub', 'cadence-errands', 'cad_evening'],
    ]);
  });

  it('rejects with the hub’s refusal on a stale revision', async () => {
    configure({
      'PATCH /api/cadences/{cadence_id}': () => stubError(409, { detail: 'the cadence changed since it was read' }),
    });
    const mutation = TestBed.runInInjectionContext(() => injectEditCadenceMutation());

    await expect(mutation.mutateAsync({ cadenceId: 'cad_evening', revision: 1, body: { name: 'e' } })).rejects.toEqual({
      detail: 'the cadence changed since it was read',
    });
  });
});
