import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectAcceptGardenProposalMutation, injectPassGardenProposalMutation } from './garden-proposal.mutations';

describe('injectPassGardenProposalMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({
      proposal_id: 'gp_1',
      routine_name: 'comments',
      class: 'x',
      title: 't',
      body: 'b',
      created_at: '2026-01-01T00:00:00Z',
      findings: [],
      closure: { closure: 'passed', reason: 'not worth it', closed_by: 'u_1', closed_at: '2026-01-02T00:00:00Z', item_outcome: null, source: null, ref: null },
    }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('passes the proposal with the given reason', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectPassGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', reason: 'not worth it' });

    const calls = stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ reason: 'not worth it' });
  });

  it('invalidates the docket list and this proposal on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectPassGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', reason: 'not worth it' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'garden-proposals']);
    expect(keys).toContainEqual(['hub', 'garden-proposal', 'gp_1']);
  });
});

describe('injectAcceptGardenProposalMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({
      proposal_id: 'gp_1',
      routine_name: 'comments',
      class: 'x',
      title: 't',
      body: 'b',
      created_at: '2026-01-01T00:00:00Z',
      findings: [],
      chunk_id: 'ch_1',
      closure: {
        closure: 'accepted',
        reason: null,
        closed_by: 'u_1',
        closed_at: '2026-01-02T00:00:00Z',
        item_outcome: 'minted',
        source: 'hub',
        ref: '42',
      },
    }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('sends mint_work_item: true with no extra fields by default', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectAcceptGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', mintWorkItem: true });

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ mint_work_item: true });
  });

  it('sends mint_work_item: false with a reason when declining to mint', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectAcceptGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', mintWorkItem: false, reason: 'already tracked elsewhere' });

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls[0].body).toEqual({ mint_work_item: false, reason: 'already tracked elsewhere' });
  });

  it('carries a body override when supplied', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectAcceptGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', mintWorkItem: true, body: 'a different body' });

    const calls = stub.forRoute('/api/garden-proposals/gp_1/accept', 'POST');
    expect(calls[0].body).toEqual({ mint_work_item: true, body: 'a different body' });
  });

  it('invalidates the docket list and this proposal on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectAcceptGardenProposalMutation());

    await mutation.mutateAsync({ proposalId: 'gp_1', mintWorkItem: true });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'garden-proposals']);
    expect(keys).toContainEqual(['hub', 'garden-proposal', 'gp_1']);
  });
});
