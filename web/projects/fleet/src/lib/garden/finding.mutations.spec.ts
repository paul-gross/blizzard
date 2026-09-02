import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import {
  injectConfirmGoneFindingsMutation,
  injectNotAFindingFindingsMutation,
  injectReopenFindingsMutation,
  injectResolveFindingsMutation,
  injectSupersedeFindingsMutation,
  injectWontFixFindingsMutation,
} from './finding.mutations';

const FINDING = {
  finding_id: 'fin_1',
  routine_name: 'comments',
  scope_slug: 'blizzard',
  class: 'stale-docstring',
  locus: 'src/a.py:1',
  summary: 'a',
  state: 'live',
  live: true,
  observed_count: 1,
  last_seen_at: '2026-01-01T00:00:00Z',
};

/** Each of these mutations' cases: send the right route + body, and invalidate both
 * the findings-bucket prefix and the by-id `hubFindingsKey` prefix on success —
 * `garden-proposal.mutations.spec.ts`'s own shape, one `describe` per verb. */
const CASES = [
  {
    name: 'injectResolveFindingsMutation',
    inject: injectResolveFindingsMutation,
    route: '/api/findings/resolve',
    vars: { findingIds: ['fin_1', 'fin_2'], note: 'landed elsewhere' },
    body: { finding_ids: ['fin_1', 'fin_2'], note: 'landed elsewhere' },
  },
  {
    name: 'injectConfirmGoneFindingsMutation',
    inject: injectConfirmGoneFindingsMutation,
    route: '/api/findings/confirm-gone',
    vars: { findingIds: ['fin_1'], note: 'no longer reproduces' },
    body: { finding_ids: ['fin_1'], note: 'no longer reproduces' },
  },
  {
    name: 'injectWontFixFindingsMutation',
    inject: injectWontFixFindingsMutation,
    route: '/api/findings/wont-fix',
    vars: { findingIds: ['fin_1'], note: "doesn't merit standing" },
    body: { finding_ids: ['fin_1'], note: "doesn't merit standing" },
  },
  {
    name: 'injectNotAFindingFindingsMutation',
    inject: injectNotAFindingFindingsMutation,
    route: '/api/findings/not-a-finding',
    vars: { findingIds: ['fin_1'], note: 'not actually a finding' },
    body: { finding_ids: ['fin_1'], note: 'not actually a finding' },
  },
  {
    name: 'injectReopenFindingsMutation',
    inject: injectReopenFindingsMutation,
    route: '/api/findings/reopen',
    vars: { findingIds: ['fin_1'], note: 'reopening' },
    body: { finding_ids: ['fin_1'], note: 'reopening' },
  },
] as const;

for (const testCase of CASES) {
  describe(testCase.name, () => {
    let stub: RequestClientStub;
    let queryClient: QueryClient;

    beforeEach(() => {
      stub = stubRequestClient(hubClient, () => [FINDING]);
      queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      TestBed.configureTestingModule({
        providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
      });
    });

    afterEach(() => stub.restore());

    it(`sends the right route and body`, async () => {
      const mutation = TestBed.runInInjectionContext(() => testCase.inject());

      await mutation.mutateAsync(testCase.vars);

      const calls = stub.forRoute(testCase.route, 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual(testCase.body);
    });

    it('invalidates both the findings-bucket prefix and the by-id findings prefix', async () => {
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
      const mutation = TestBed.runInInjectionContext(() => testCase.inject());

      await mutation.mutateAsync(testCase.vars);

      const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
      expect(keys).toContainEqual(['hub', 'findings-bucket']);
      expect(keys).toContainEqual(['hub', 'findings']);
    });
  });
}

describe('injectSupersedeFindingsMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => [FINDING]);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('sends finding_ids, note, and superseded_by', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectSupersedeFindingsMutation());

    await mutation.mutateAsync({ findingIds: ['fin_1'], note: 'superseded', supersededBy: 'fin_2' });

    const calls = stub.forRoute('/api/findings/supersede', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ finding_ids: ['fin_1'], note: 'superseded', superseded_by: 'fin_2' });
  });

  it('invalidates both the findings-bucket prefix and the by-id findings prefix', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectSupersedeFindingsMutation());

    await mutation.mutateAsync({ findingIds: ['fin_1'], note: 'superseded', supersededBy: 'fin_2' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'findings-bucket']);
    expect(keys).toContainEqual(['hub', 'findings']);
  });
});
