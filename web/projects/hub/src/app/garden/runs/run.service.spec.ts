import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type RequestClientStub, routineRunResponse, stubError, stubHubClient } from 'fleet/testing';
import { EMPTY_RUN_DRAFT } from '../core/run.domain';
import { injectRunService } from './run.service';

describe('injectRunService', () => {
  let stub: RequestClientStub;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    return TestBed.runInInjectionContext(() => injectRunService());
  }

  afterEach(() => stub.restore());

  it('runs the routine from the draft and answers the run the hub minted', async () => {
    const service = configure({
      'POST /api/routines/{routine_id}/run': () => routineRunResponse({ chunk_id: 'ch_1', created_at: '2026-01-01T00:00:00Z' }),
    });

    const outcome = await service.run('rtn_1', { ...EMPTY_RUN_DRAFT, scope: 'web', mode: 'delta', urgency: 'urgent', note: ' hi ' });

    expect(outcome.ok && outcome.run.chunkId).toBe('ch_1');
    expect(stub.forRoute('/api/routines/rtn_1/run', 'POST')[0].body).toEqual({
      scope_slug: 'web',
      mode: 'delta',
      note: 'hi',
      urgency: 'urgent',
    });
  });

  it('answers a field refusal on its field, never throwing', async () => {
    const field = configure({
      'POST /api/routines/{routine_id}/run': () =>
        stubError(422, { detail: [{ loc: ['body', 'scope_slug'], msg: 'unknown scope', type: 'value_error' }] }),
    });
    expect(await field.run('rtn_1', { ...EMPTY_RUN_DRAFT, scope: 'x' })).toEqual({ ok: false, fields: { scope: 'unknown scope' }, form: null });
  });

  it('answers any other refusal on the form line', async () => {
    const line = configure({ 'POST /api/routines/{routine_id}/run': () => stubError(503, { detail: "scope 'web' is retired" }) });
    const outcome = await line.run('rtn_1', { ...EMPTY_RUN_DRAFT, scope: 'web' });
    expect(outcome).toMatchObject({ ok: false, fields: {} });
    expect(!outcome.ok && outcome.form).toContain('retired');
  });
});
