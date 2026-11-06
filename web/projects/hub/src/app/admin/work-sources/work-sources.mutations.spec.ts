import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubConfigKey } from 'fleet';
import { type RequestClientStub, stubHubClient, workSourceSummary } from 'fleet/testing';
import {
  injectCreateWorkSourceMutation,
  injectEditWorkSourceMutation,
  injectWorkSourceLifecycleMutation,
} from './work-sources.mutations';

describe('work source mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'POST /api/work-sources': () => workSourceSummary({ work_source_id: 'a', slug: 'a' }),
      'PATCH /api/work-sources/{source}': () => workSourceSummary({ work_source_id: 'a', revision: 4 }),
      'POST /api/work-sources/{source}/retire': () => workSourceSummary({ work_source_id: 'a', retired: true }),
      'POST /api/work-sources/{source}/enable': () => workSourceSummary({ work_source_id: 'a' }),
    });
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: unknown }).queryKey);
  }

  it('edits with the shown revision as If-Match, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectEditWorkSourceMutation());

    await mutation.mutateAsync({
      workSourceId: 'a',
      revision: 3,
      body: { locator: null },
    });

    const calls = stub.forRoute('/api/work-sources/a', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ locator: null });
    expect(calls[0].headers['if-match']).toBe('3');
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });

  it('routes retire and enable by the desired state, each sending If-Match', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectWorkSourceLifecycleMutation());

    await mutation.mutateAsync({ workSourceId: 'a', revision: 2, retired: true });
    await mutation.mutateAsync({ workSourceId: 'a', revision: 3, retired: false });

    expect(stub.forRoute('/api/work-sources/a/retire', 'POST')[0].headers['if-match']).toBe('2');
    expect(stub.forRoute('/api/work-sources/a/enable', 'POST')[0].headers['if-match']).toBe('3');
  });

  it('creates, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateWorkSourceMutation());

    await mutation.mutateAsync({
      slug: 'a',
      provider: 'github',
      locator: 'o/r',
    });

    expect(stub.forRoute('/api/work-sources', 'POST')).toHaveLength(1);
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });
});
