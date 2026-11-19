import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenRoutineView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { EMPTY_ROUTINE_CREATE_DRAFT } from '../core/routine.domain';
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

    expect(await service.create({ ...DRAFT, slug: ' nightly ', name: ' ' })).toEqual({ ok: true });

    const calls = stub.forRoute('/api/projects/acme/routines', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBeUndefined();
    expect(calls[0].body).toEqual({
      slug: 'nightly',
      name: null,
      graph_name: 'garden',
      default_scope_slug: 'web',
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
});
