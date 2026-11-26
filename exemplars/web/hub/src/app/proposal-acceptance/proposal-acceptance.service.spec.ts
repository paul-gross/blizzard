// exemplar: bzh:frontend-mutation-settles-on-refresh — the accept's outcomes asserted.

import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { gardenProposalAcceptResponse, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';

import { acceptanceDraftFor } from './proposal-acceptance.domain';
import { injectProposalAcceptanceService } from './proposal-acceptance.service';

@Component({ selector: 'app-acceptance-service-host', template: '' })
class Host {
  readonly service = injectProposalAcceptanceService();
}

function setup(routes: Parameters<typeof stubHubClient>[0]) {
  const stub = stubHubClient(routes);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
  });
  const fixture = TestBed.createComponent(Host);
  return { stub, queryClient, service: fixture.componentInstance.service };
}

const ROUTE = '/api/garden-proposals/gp_1/accept';

describe('injectProposalAcceptanceService', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('fires the accept once, resolving only after the three invalidations', async () => {
    const s = setup({
      'POST /api/garden-proposals/{proposal_id}/accept': () => gardenProposalAcceptResponse({ proposal_id: 'gp_1' }),
    });
    stub = s.stub;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const invalidate = vi.spyOn(s.queryClient, 'invalidateQueries').mockImplementation(() => gate);

    let settled = false;
    const pending = s.service.accept('gp_1', acceptanceDraftFor(' the body ')).then((o) => {
      settled = true;
      return o;
    });
    await vi.waitFor(() => expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(settled).toBe(false);
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(stub.forRoute(ROUTE, 'POST')[0].body).toEqual({ mint_work_item: true, body: 'the body' });

    release();
    expect(await pending).toEqual({ ok: true });
  });

  it('refuses a decline with a blank reason on the reason field without sending anything', async () => {
    const s = setup({});
    stub = s.stub;
    expect(await s.service.accept('gp_1', { ...acceptanceDraftFor('b'), mode: 'decline', reason: '  ' })).toEqual({
      ok: false,
      fields: { reason: 'Required' },
      form: null,
    });
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(0);
  });

  it('answers a raced closure as a form-level SaveOutcome without retrying', async () => {
    const s = setup({
      'POST /api/garden-proposals/{proposal_id}/accept': () =>
        stubError(409, {
          type: 'urn:blizzard:problem:garden-proposal-already-closed',
          detail: 'already closed',
        }),
    });
    stub = s.stub;
    expect(await s.service.accept('gp_1', acceptanceDraftFor('b'))).toEqual({
      ok: false,
      fields: {},
      form: 'already closed',
    });
    expect(stub.forRoute(ROUTE, 'POST')).toHaveLength(1);
  });
});
