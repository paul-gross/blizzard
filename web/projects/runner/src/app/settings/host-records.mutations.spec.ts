import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hostSubscriptionView, hostWorkspaceView, type RequestClientStub, stubRunnerClient } from 'fleet/testing';
import { hostSettingsChangesKey, hostSubscriptionsKey, hostWorkspacesKey } from '../core/query-keys';
import {
  injectRecordCreateMutation,
  injectRecordEditMutation,
  injectRecordEnableMutation,
  injectRecordRetireMutation,
  recordInvalidations,
} from './host-records.mutations';

@Component({ selector: 'app-test-record-mutations-host', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class MutationsHost {
  readonly create = injectRecordCreateMutation(() => 'workspace');
  readonly edit = injectRecordEditMutation(() => 'workspace');
  readonly retire = injectRecordRetireMutation(() => 'subscription');
  readonly enable = injectRecordEnableMutation(() => 'subscription');
}

const MAX = { slug: 'max', name: 'Max', provider: 'anthropic' };

describe('host record mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  afterEach(() => stub?.restore());

  function configure() {
    stub = stubRunnerClient({
      'POST /api/host/workspaces': () => hostWorkspaceView(),
      'PATCH /api/host/workspaces/{workspace_id}': () => hostWorkspaceView(),
      'POST /api/host/subscriptions/{slug}/retire': () => hostSubscriptionView({ ...MAX, state: 'retired' }),
      'POST /api/host/subscriptions/{slug}/enable': () => hostSubscriptionView(MAX),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ imports: [MutationsHost], providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    const fixture = TestBed.createComponent(MutationsHost);
    fixture.detectChanges();
    return { host: fixture.componentInstance, tick: () => fixture.detectChanges() };
  }

  it('re-reads every read of the kind — the Host tab`s and the Settings tab`s — and the host`s change log', () => {
    expect(recordInvalidations('workspace')).toEqual([hostWorkspacesKey, hostSettingsChangesKey]);
    expect(recordInvalidations('subscription')).toEqual([hostSubscriptionsKey, hostSettingsChangesKey]);
  });

  it('stays pending until its invalidations have refetched, writing nothing to the cache', async () => {
    const { host, tick } = configure();
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const setData = vi.spyOn(queryClient, 'setQueryData');
    const create = host.create;

    const done = create.mutateAsync({ kind: 'workspace', body: { id: 'web' } });
    await vi.waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: hostWorkspacesKey }));
    expect(spy).toHaveBeenCalledWith({ queryKey: hostSettingsChangesKey });
    tick();
    expect(create.isPending()).toBe(true);
    release();
    await done;
    await vi.waitFor(() => {
      tick();
      expect(create.isPending()).toBe(false);
    });
    expect(setData).not.toHaveBeenCalled();
    expect(stub.forRoute('/api/host/workspaces', 'POST')[0].body).toEqual({ id: 'web' });
  });

  it('holds only its own verb pending', async () => {
    const { host, tick } = configure();
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const { edit, retire, enable } = host;

    const done = retire.mutateAsync({ kind: 'subscription', key: 'max' });
    await vi.waitFor(() => {
      tick();
      expect(retire.isPending()).toBe(true);
    });
    expect(edit.isPending()).toBe(false);
    expect(enable.isPending()).toBe(false);
    release();
    await done;
    expect(stub.forRoute('/api/host/subscriptions/max/retire', 'POST')).toHaveLength(1);
  });

  it('edits against the revision as If-Match', async () => {
    const { edit } = configure().host;

    await edit.mutateAsync({ kind: 'workspace', key: 'web', revision: 3, body: { root: null } });

    const calls = stub.forRoute('/api/host/workspaces/web', 'PATCH');
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ root: null });
  });
});
