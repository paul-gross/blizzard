import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type RequestClientStub, runnerSettingsView, stubError, stubRunnerClient } from 'fleet/testing';
import type { SettingsTarget } from './settings.domain';
import { injectSettingsService } from './settings.service';

const RUNNER: SettingsTarget = { kind: 'runner', runnerId: 'rn_1' };
const BASELINE = { id: 'rn_1', revision: 3, draft: { value: 'null' } };
const BUDGET = { key: 'throttle.budget.usd', current: null };

describe('injectSettingsService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => stub.restore());

  function configure(routes: Parameters<typeof stubRunnerClient>[0], target: SettingsTarget = RUNNER) {
    stub = stubRunnerClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    return TestBed.runInInjectionContext(() => injectSettingsService(() => target));
  }

  it('sets the key to the value its text reads as, the baseline revision as If-Match', async () => {
    const service = configure({ 'PATCH /runners/{runner_id}/api/settings': () => runnerSettingsView() });

    expect(await service.set(BUDGET, BASELINE, { value: '25' })).toEqual({ ok: true });

    const calls = stub.forRoute('/runners/rn_1/api/settings', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ set: { 'throttle.budget.usd': 25 } });
  });

  it('resets the host`s key to its default through the host route', async () => {
    const service = configure({ 'PATCH /api/host/settings': () => runnerSettingsView() }, { kind: 'host' });

    expect(await service.unset({ key: 'max_agents', current: 6 }, { ...BASELINE, id: 'host' })).toEqual({ ok: true });

    const calls = stub.forRoute('/api/host/settings', 'PATCH');
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ unset: ['max_agents'] });
  });

  it('sends nothing for text that does not read as a value', async () => {
    const service = configure({ 'PATCH /runners/{runner_id}/api/settings': () => runnerSettingsView() });

    expect(await service.set(BUDGET, BASELINE, { value: '{x' })).toEqual({
      ok: false,
      fields: { value: 'Enter a valid JSON value.' },
      form: null,
    });
    expect(stub.forRoute('/runners/rn_1/api/settings', 'PATCH')).toHaveLength(0);
  });

  it('answers a revision conflict on the form line, sent once', async () => {
    const service = configure({
      'PATCH /runners/{runner_id}/api/settings': () =>
        stubError(409, {
          type: 'urn:blizzard:problem:revision-conflict',
          title: 'revision conflict',
          status: 409,
          detail: 'the record moved',
          code: 'revision_conflict',
          key: null,
          current: 4,
          errors: null,
          record_kind: null,
          record_key: null,
        }),
    });

    expect(await service.set(BUDGET, BASELINE, { value: '25' })).toEqual({ ok: false, fields: {}, form: 'the record moved' });
    expect(stub.forRoute('/runners/rn_1/api/settings', 'PATCH')).toHaveLength(1);
  });

  it('resolves a write only once the invalidation has refetched', async () => {
    const service = configure({ 'PATCH /runners/{runner_id}/api/settings': () => runnerSettingsView() });
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);

    let done = false;
    const saved = service.set(BUDGET, BASELINE, { value: '25' }).then(() => (done = true));
    await vi.waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['rn_1', 'settings'] }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });
});
