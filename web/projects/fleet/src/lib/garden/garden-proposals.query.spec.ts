import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { settle } from '../testing/settle';
import { injectHubGardenProposalsQuery, isGardenProposalWaiting } from './garden-proposals.query';

describe('isGardenProposalWaiting', () => {
  it('is waiting when the proposal carries no closure', () => {
    expect(isGardenProposalWaiting({ closure: null } as never)).toBe(true);
    expect(isGardenProposalWaiting({} as never)).toBe(true);
  });

  it('is not waiting once a closure is recorded', () => {
    expect(isGardenProposalWaiting({ closure: { closure: 'passed' } } as never)).toBe(false);
  });
});

@Component({
  selector: 'fleet-test-garden-proposals-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestGardenProposalsQueryHost {
  readonly query = injectHubGardenProposalsQuery();
}

describe('injectHubGardenProposalsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the docket off GET /api/garden-proposals — one request when next_cursor is already null', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') {
        return {
          proposals: [
            { proposal_id: 'gp_1', routine_name: 'comments', class: 'x', body: 'b', created_at: '2026-01-01T00:00:00Z', findings: [] },
          ],
          next_cursor: null,
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestGardenProposalsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestGardenProposalsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toHaveLength(1);
    expect(stub.forRoute('/api/garden-proposals', 'GET')).toHaveLength(1);
  });

  it('drains a multi-page docket and concatenates the pages in order (blizzard#526)', async () => {
    let calls = 0;
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') {
        calls += 1;
        return calls === 1
          ? {
              proposals: [
                { proposal_id: 'gp_1', routine_name: 'comments', class: 'x', body: 'b', created_at: '2026-01-01T00:00:00Z', findings: [] },
              ],
              next_cursor: 'cursor-1',
            }
          : {
              proposals: [
                { proposal_id: 'gp_2', routine_name: 'comments', class: 'x', body: 'b', created_at: '2026-01-01T00:00:00Z', findings: [] },
              ],
              next_cursor: null,
            };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestGardenProposalsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestGardenProposalsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.map((p) => p.proposal_id)).toEqual(['gp_1', 'gp_2']);
    expect(stub.forRoute('/api/garden-proposals', 'GET')).toHaveLength(2);
  });
});
