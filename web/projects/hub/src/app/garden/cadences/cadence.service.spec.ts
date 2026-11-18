import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenCadenceView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { lensedKey } from '../../core/query-keys';
import { gardenCadence } from '../core/cadence.builders';
import { EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { fromCadenceView, toEditBaseline } from '../core/cadence.translate';
import { hubCadencesKey } from '../core/cadences.query';
import { injectCadenceService } from './cadence.service';

describe('injectCadenceService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    });
    return TestBed.runInInjectionContext(() => injectCadenceService());
  }

  afterEach(() => stub.restore());

  it('sends the baseline revision as If-Match after a query refresh moved the cached one', async () => {
    const service = configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening', revision: 6 }) });
    const key = lensedKey(hubCadencesKey, 'acme');
    queryClient.setQueryData(key, [gardenCadenceView({ slug: 'evening', revision: 3 })]);
    const baseline = toEditBaseline(fromCadenceView(queryClient.getQueryData<ReturnType<typeof gardenCadenceView>[]>(key)![0]));
    queryClient.setQueryData(key, [gardenCadenceView({ slug: 'evening', revision: 5 })]);

    const outcome = await service.edit(baseline, { ...baseline.draft, name: 'Evening sweep' });

    expect(outcome).toEqual({ ok: true });
    const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ name: 'Evening sweep' });
  });

  it('answers a revision conflict on the form line, sends once, and leaves the baseline and draft as they were', async () => {
    const service = configure({
      'PATCH /api/cadences/{cadence_id}': () =>
        stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' }),
    });
    const baseline = toEditBaseline(gardenCadence({ slug: 'evening', revision: 3 }));
    const before = structuredClone(baseline);
    const draft = { ...baseline.draft, cron: '0 21 * * *' };
    const typed = { ...draft };

    const outcome = await service.edit(baseline, draft);

    expect(outcome).toEqual({ ok: false, fields: {}, form: 'the cadence changed since it was read' });
    expect(stub.forRoute(`/api/cadences/${baseline.id}`, 'PATCH')).toHaveLength(1);
    expect(baseline).toEqual(before);
    expect(draft).toEqual(typed);
  });

  it('resolves a save only once the invalidations have refetched', async () => {
    const service = configure({ 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening' }) });
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const baseline = toEditBaseline(gardenCadence({ slug: 'evening' }));

    let done = false;
    const saved = service.edit(baseline, { ...baseline.draft, name: 'e' }).then(() => (done = true));
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(3));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });

  it('answers a field refusal on its field, never throwing', async () => {
    const service = configure({
      'POST /api/projects/{project}/cadences': () =>
        stubError(422, { detail: [{ loc: ['body', 'schedule', 'cron'], msg: 'a cron expression has five fields', type: 'value_error' }] }),
    });

    const outcome = await service.create({ ...EMPTY_CADENCE_DRAFT, slug: 'evening', cron: '0 20 * *', zone: 'UTC' });

    expect(outcome).toEqual({ ok: false, fields: { cron: 'a cron expression has five fields' }, form: null });
  });

  it('answers a slug already taken on the slug field', async () => {
    const service = configure({
      'POST /api/projects/{project}/cadences': () =>
        stubError(422, { type: 'urn:blizzard:problem:cadence-slug-taken', detail: 'the project already holds slug evening' }),
    });

    const outcome = await service.create({ ...EMPTY_CADENCE_DRAFT, slug: 'evening', spacing: '1d' });

    expect(outcome).toEqual({ ok: false, fields: { slug: 'the project already holds slug evening' }, form: null });
  });

  it('creates the trimmed draft and answers ok', async () => {
    const service = configure({ 'POST /api/projects/{project}/cadences': () => gardenCadenceView({ slug: 'evening' }) });

    expect(await service.create({ ...EMPTY_CADENCE_DRAFT, slug: ' evening ', spacing: '1d' })).toEqual({ ok: true });
    expect(stub.forRoute('/api/projects/acme/cadences', 'POST')[0].body).toEqual({ slug: 'evening', spacing: '1d' });
  });
});
