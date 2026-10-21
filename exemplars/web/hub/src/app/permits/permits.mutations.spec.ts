import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, QueryObserver, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type PermitView, hubClient, hubPermitKey, hubPermitsKey } from 'fleet';
import { type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { injectPermitMoveMutation, injectPermitRevokeMutation } from './permits.mutations';

const GRANTED: PermitView = {
  permit_id: 'pmt_1',
  project_id: 'prj_core',
  project_slug: 'core',
  project_name: 'Core',
  holder: 'ada',
  capability: 'deploy',
  state: 'granted',
  verbs: ['suspend', 'revoke'],
  requested_at: '2026-10-01T09:00:00Z',
  requested_by: 'usr_grace',
  changed_at: '2026-10-02T09:00:00Z',
  changed_by: 'usr_grace',
  revocation_note: null,
};

describe('permit mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  function invalidatedKeys(spy: { mock: { calls: readonly (readonly unknown[])[] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
  }

  it('POSTs the verb its vars name and re-reads the listing and the permit', async () => {
    stub = stubRequestClient(hubClient, () => GRANTED);
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectPermitMoveMutation());

    await mutation.mutateAsync({ permitId: 'pmt_1', verb: 'grant' });

    expect(stub.forRoute('/api/permits/pmt_1/grant', 'POST')).toHaveLength(1);
    expect(invalidatedKeys(invalidateSpy)).toEqual([hubPermitsKey, hubPermitKey('pmt_1')]);
  });

  it('stays pending until the active listing has refetched', async () => {
    let releaseListing: () => void = () => undefined;
    stub = stubRequestClient(hubClient, () => GRANTED);
    const listing = new QueryObserver(queryClient, {
      queryKey: [...hubPermitsKey, 'ada'],
      queryFn: () => new Promise<PermitView[]>((resolve) => (releaseListing = () => resolve([GRANTED]))),
    });
    const unsubscribe = listing.subscribe(() => undefined);
    releaseListing();
    await vi.waitFor(() => expect(listing.getCurrentResult().isSuccess).toBe(true));
    const mutation = TestBed.runInInjectionContext(() => injectPermitMoveMutation());
    // The hook subscribes to its observer in an effect; flush it so `isPending()` tracks the mutation.
    TestBed.tick();

    const settled = mutation.mutateAsync({ permitId: 'pmt_1', verb: 'suspend' });
    await vi.waitFor(() => expect(listing.getCurrentResult().isFetching).toBe(true));
    expect(mutation.isPending()).toBe(true);

    releaseListing();
    await settled;
    await vi.waitFor(() => expect(mutation.isPending()).toBe(false));
    unsubscribe();
  });

  it('rejects a refused verb (409, not from that state)', async () => {
    stub = stubRequestClient(hubClient, () => stubError(409, { detail: 'permit pmt_1 is granted — grant refused' }));
    const mutation = TestBed.runInInjectionContext(() => injectPermitMoveMutation());

    await expect(mutation.mutateAsync({ permitId: 'pmt_1', verb: 'grant' })).rejects.toBeTruthy();
  });

  it('POSTs a revocation with its note', async () => {
    stub = stubRequestClient(hubClient, () => ({ ...GRANTED, state: 'revoked', verbs: [] }));
    const mutation = TestBed.runInInjectionContext(() => injectPermitRevokeMutation());

    await mutation.mutateAsync({ permitId: 'pmt_1', note: 'left the team' });

    const requests = stub.forRoute('/api/permits/pmt_1/revoke', 'POST');
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({ note: 'left the team' });
  });
});
