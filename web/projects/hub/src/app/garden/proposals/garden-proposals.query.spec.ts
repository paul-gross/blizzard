import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { gardenProposalView, gardenProposalsPageView, type RequestClientStub, settle, stubHubClient } from 'fleet/testing';
import { injectHubGardenProposalsQuery } from './garden-proposals.query';

@Component({
  selector: 'app-test-garden-proposals-query-host',
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
    stub = stubHubClient({
      'GET /api/garden-proposals': () =>
        gardenProposalsPageView({ proposals: [gardenProposalView({ proposal_id: 'gp_1', class: 'x', body: 'b' })] }),
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
    stub = stubHubClient({
      'GET /api/garden-proposals': () => {
        calls += 1;
        return calls === 1
          ? gardenProposalsPageView({
              proposals: [gardenProposalView({ proposal_id: 'gp_1', class: 'x', body: 'b' })],
              next_cursor: 'cursor-1',
            })
          : gardenProposalsPageView({ proposals: [gardenProposalView({ proposal_id: 'gp_2', class: 'x', body: 'b' })] });
      },
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
