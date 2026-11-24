import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenRoutineView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { EMPTY_ROUTINE_CREATE_DRAFT } from '../core/routine.domain';
import { toEditBaseline } from '../core/routine.translate';
import { gardenRoutine } from '../core/routine.builders';
import { injectRoutineService } from './routine.service';

const DRAFT = { ...EMPTY_ROUTINE_CREATE_DRAFT, slug: 'nightly', graph: 'garden', scope: 'web' };

describe('injectRoutineService', () => {
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
    return TestBed.runInInjectionContext(() => injectRoutineService());
  }

  afterEach(() => stub.restore());

  it('creates the translated draft, unversioned, and answers ok', async () => {
    const service = configure({ 'POST /api/projects/{project}/routines': () => gardenRoutineView({ routine_id: 'rtn_new', name: 'nightly', slug: 'nightly' }) });

    expect(await service.create({ ...DRAFT, slug: ' nightly ', scope: 'web\n', name: ' ' })).toEqual({ ok: true });

    const calls = stub.forRoute('/api/projects/acme/routines', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBeUndefined();
    expect(calls[0].body).toEqual({
      slug: ' nightly ',
      name: null,
      graph_name: 'garden',
      default_scope_slug: 'web\n',
      default_urgency: null,
    });
  });

  it('answers each config-field entry on its own field', async () => {
    const service = configure({
      'POST /api/projects/{project}/routines': () =>
        stubError(422, {
          type: 'urn:blizzard:problem:config-field-invalid',
          detail: [
            { loc: ['body', 'graph_name'], msg: 'a graph name is required', type: 'value_error' },
            { loc: ['body', 'slug'], msg: 'slug is malformed', type: 'value_error' },
          ],
        }),
    });

    expect(await service.create(DRAFT)).toEqual({
      ok: false,
      fields: { graph: 'a graph name is required', slug: 'slug is malformed' },
      form: null,
    });
  });

  it('answers a malformed scope slug on the scope field', async () => {
    const service = configure({
      'POST /api/projects/{project}/routines': () =>
        stubError(422, { type: 'urn:blizzard:problem:scope-slug-invalid', detail: 'scope slug is malformed' }),
    });

    expect(await service.create(DRAFT)).toEqual({ ok: false, fields: { scope: 'scope slug is malformed' }, form: null });
  });

  it('answers a definition refusal on the form line', async () => {
    const service = configure({
      'POST /api/projects/{project}/routines': () =>
        stubError(422, { type: 'urn:blizzard:problem:routine-definition-invalid', detail: 'the project already holds slug nightly' }),
    });

    expect(await service.create(DRAFT)).toEqual({ ok: false, fields: {}, form: 'the project already holds slug nightly' });
  });

  it('resolves a save only once the invalidations have refetched', async () => {
    const service = configure({ 'POST /api/projects/{project}/routines': () => gardenRoutineView({ routine_id: 'rtn_new', name: 'nightly', slug: 'nightly' }) });
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);

    let done = false;
    const saved = service.create(DRAFT).then(() => (done = true));
    await vi.waitFor(() => expect(queryClient.invalidateQueries).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });

  describe('edit', () => {
    const baseline = toEditBaseline(gardenRoutine({ slug: 'nightly', id: 'rtn_1', revision: 3 }))!;

    it('sends only the changed fields against the baseline revision, and answers ok', async () => {
      const service = configure({ 'PATCH /api/routines/{routine_id}': () => gardenRoutineView({ routine_id: 'rtn_1', name: 'n' }) });

      expect(await service.edit(baseline, { ...baseline.draft, graph: ' garden-delta ' })).toEqual({ ok: true });

      const calls = stub.forRoute('/api/routines/rtn_1', 'PATCH');
      expect(calls[0].body).toEqual({ graph_name: 'garden-delta' });
      expect(calls[0].headers['if-match']).toBe('3');
    });

    it('sends nothing for a save that changes nothing', async () => {
      const service = configure({});

      expect(await service.edit(baseline, { ...baseline.draft })).toEqual({ ok: true });
      expect(stub.forRoute('/api/routines/rtn_1', 'PATCH')).toHaveLength(0);
    });

    it('answers a config-field entry on its field and an unresolved graph on the changed field', async () => {
      const service = configure({
        'PATCH /api/routines/{routine_id}': () =>
          stubError(422, { type: 'urn:blizzard:problem:routine-definition-invalid', detail: 'no graph is named nope' }),
      });

      expect(await service.edit(baseline, { ...baseline.draft, graph: 'nope' })).toEqual({
        ok: false,
        fields: { graph: 'no graph is named nope' },
        form: null,
      });
    });

    it('answers a revision conflict at form level, keeping the baseline and never retrying', async () => {
      const service = configure({
        'PATCH /api/routines/{routine_id}': () =>
          stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the routine changed since it was read' }),
      });
      const before = structuredClone(baseline);

      const outcome = await service.edit(baseline, { ...baseline.draft, graph: 'garden-delta' });

      expect(outcome).toEqual({ ok: false, fields: {}, form: 'the routine changed since it was read' });
      expect(stub.forRoute('/api/routines/rtn_1', 'PATCH')).toHaveLength(1);
      expect(baseline).toEqual(before);
    });

    it('sends the original baseline revision even after the routines read refreshed', async () => {
      const service = configure({ 'PATCH /api/routines/{routine_id}': () => gardenRoutineView({ routine_id: 'rtn_1', name: 'n' }) });
      queryClient.setQueryData(['hub', 'routines'], [gardenRoutineView({ routine_id: 'rtn_1', name: 'n', revision: 9 })]);

      await service.edit(baseline, { ...baseline.draft, urgency: 'urgent' });

      expect(stub.forRoute('/api/routines/rtn_1', 'PATCH')[0].headers['if-match']).toBe('3');
    });
  });
});
