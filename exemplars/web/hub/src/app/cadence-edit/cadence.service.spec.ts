// exemplar: bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh — the save's outcomes asserted.

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenCadenceView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { editCadence } from './cadence.builders';
import { injectCadenceService } from './cadence.service';
import { toEditBaseline } from './cadence.translate';

describe('injectCadenceService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => stub.restore());

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    return TestBed.runInInjectionContext(() => injectCadenceService());
  }

  it('sends the baseline revision as If-Match and only the changed fields', async () => {
    const service = configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening' }) });
    const baseline = toEditBaseline(editCadence({ revision: 3 }));

    expect(await service.edit(baseline, { ...baseline.draft, name: 'Evening sweep' })).toEqual({ ok: true });

    const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ name: 'Evening sweep' });
  });

  it('answers a revision conflict on the form line, sent once', async () => {
    const service = configure({
      'PATCH /api/cadences/{cadence_id}': () =>
        stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' }),
    });
    const baseline = toEditBaseline(editCadence());

    const outcome = await service.edit(baseline, { ...baseline.draft, cron: '0 21 * * *' });

    expect(outcome).toEqual({ ok: false, fields: {}, form: 'the cadence changed since it was read' });
    expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(1);
  });

  it('resolves a save only once the invalidation has refetched', async () => {
    const service = configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening' }) });
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const baseline = toEditBaseline(editCadence());

    let done = false;
    const saved = service.edit(baseline, { ...baseline.draft, name: 'e' }).then(() => (done = true));
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });
});
