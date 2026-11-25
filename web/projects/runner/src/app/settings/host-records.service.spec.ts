import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hostSubscriptionView, hostWorkspaceView, type RequestClientStub, stubError, stubRunnerClient } from 'fleet/testing';
import type { HostRecordKind } from './host-records.domain';
import { injectHostRecordsService } from './host-records.service';

const CONFLICT = {
  type: 'urn:blizzard:problem:revision-conflict',
  title: 'revision conflict',
  status: 409,
  detail: 'the workspace moved',
  code: 'revision_conflict',
  key: null,
  current: 4,
  errors: null,
  record_kind: 'workspace',
  record_key: 'web',
};

const MAX = { slug: 'max', name: 'Max', provider: 'anthropic' };

describe('injectHostRecordsService', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  function configure(routes: Parameters<typeof stubRunnerClient>[0], kind: HostRecordKind = 'workspace') {
    stub = stubRunnerClient(routes);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    return TestBed.runInInjectionContext(() => injectHostRecordsService(() => kind));
  }

  it('creates a workspace stating its non-blank fields', async () => {
    const service = configure({ 'POST /api/host/workspaces': () => hostWorkspaceView() });

    expect(await service.create({ id: 'web', root: '/srv/web', envs: 'a, b', max_environments: '' })).toEqual({ ok: true });

    expect(stub.forRoute('/api/host/workspaces', 'POST')[0].body).toEqual({ id: 'web', root: '/srv/web', envs: ['a', 'b'] });
  });

  it('sends nothing for a draft that does not read, answering each field', async () => {
    const service = configure({ 'POST /api/host/subscriptions': () => hostSubscriptionView(MAX) }, 'subscription');

    expect(await service.create({ slug: 'max', name: 'Max', provider: '', sample_interval_seconds: 'soon' })).toEqual({
      ok: false,
      fields: { provider: 'Required.', sample_interval_seconds: 'Enter a whole number.' },
      form: null,
    });
    expect(stub.forRoute('/api/host/subscriptions', 'POST')).toHaveLength(0);
  });

  it('edits only the changed keys, the baseline revision as If-Match', async () => {
    const service = configure({ 'PATCH /api/host/subscriptions/{slug}': () => hostSubscriptionView(MAX) }, 'subscription');
    const before = { id: 'max', revision: 4, draft: { name: 'Max', credentials_path: '/c', sample_interval_seconds: '60', usage_api_base: '' } };

    expect(await service.edit(before, { ...before.draft, credentials_path: '', sample_interval_seconds: '300' })).toEqual({ ok: true });

    const calls = stub.forRoute('/api/host/subscriptions/max', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('4');
    expect(calls[0].body).toEqual({ credentials_path: null, sample_interval_seconds: 300 });
  });

  it('answers a revision conflict on the form line, sent once', async () => {
    const service = configure({ 'PATCH /api/host/workspaces/{workspace_id}': () => stubError(409, CONFLICT) });
    const before = { id: 'web', revision: 3, draft: { root: '/a' } };

    expect(await service.edit(before, { root: '/b' })).toEqual({ ok: false, fields: {}, form: 'the workspace moved' });
    expect(stub.forRoute('/api/host/workspaces/web', 'PATCH')).toHaveLength(1);
  });

  it('retires and enables through their own routes, a refusal answered on the form line', async () => {
    const service = configure({
      'POST /api/host/workspaces/{workspace_id}/retire': () => hostWorkspaceView({ state: 'retired' }),
      'POST /api/host/workspaces/{workspace_id}/enable': () =>
        stubError(422, { ...CONFLICT, status: 422, code: 'settings_invalid', detail: 'root is gone' }),
    });

    expect(await service.retire('web')).toEqual({ ok: true });
    expect(stub.forRoute('/api/host/workspaces/web/retire', 'POST')).toHaveLength(1);
    expect(await service.enable('web')).toEqual({ ok: false, fields: {}, form: 'root is gone' });
  });
});
