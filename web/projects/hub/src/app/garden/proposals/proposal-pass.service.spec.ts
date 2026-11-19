import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { gardenProposalView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';

import { injectProposalPassService } from './proposal-pass.service';

@Component({ selector: 'app-pass-service-host', template: '' })
class Host {
  readonly service = injectProposalPassService();
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

describe('injectProposalPassService', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('fires the pass once with the trimmed reason, resolving only after the invalidations', async () => {
    const s = setup({
      'POST /api/garden-proposals/{proposal_id}/pass': () => gardenProposalView({ proposal_id: 'gp_1' }),
    });
    stub = s.stub;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.spyOn(s.queryClient, 'invalidateQueries').mockImplementation(() => gate);

    let settled = false;
    const pending = s.service.pass('gp_1', { reason: ' why ' }).then((o) => {
      settled = true;
      return o;
    });
    await vi.waitFor(() => expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(settled).toBe(false);
    expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')[0].body).toEqual({ reason: 'why' });

    release();
    expect(await pending).toEqual({ ok: true });
  });

  it('answers a raced closure as a form-level SaveOutcome without retrying', async () => {
    const s = setup({
      'POST /api/garden-proposals/{proposal_id}/pass': () =>
        stubError(409, {
          type: 'urn:blizzard:problem:garden-proposal-already-closed',
          detail: 'already closed',
        }),
    });
    stub = s.stub;
    expect(await s.service.pass('gp_1', { reason: 'why' })).toEqual({
      ok: false,
      fields: {},
      form: 'already closed',
    });
    expect(stub.forRoute('/api/garden-proposals/gp_1/pass', 'POST')).toHaveLength(1);
  });
});
