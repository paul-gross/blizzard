import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectHubFindingQuery } from './finding.query';

@Component({
  selector: 'fleet-test-finding-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestFindingQueryHost {
  readonly findingId = signal<string | null>(null);
  readonly query = injectHubFindingQuery(() => this.findingId());
}

describe('injectHubFindingQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('stays disabled while no finding is selected', async () => {
    stub = stubRequestClient(hubClient, () => ({}));
    TestBed.configureTestingModule({
      imports: [TestFindingQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.forRoute('/api/findings/fin_1', 'GET')).toHaveLength(0);
  });

  it('reads one finding off GET /api/findings/{finding_id}', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/findings/fin_1') {
        return {
          finding_id: 'fin_1',
          routine_name: 'comments',
          scope_slug: 'blizzard',
          class: 'stale-docstring',
          locus: 'src/x.py:1',
          summary: 'stale',
          state: 'live',
          live: true,
          observed_count: 1,
          last_seen_at: '2026-01-01T00:00:00Z',
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestFindingQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingQueryHost);
    fixture.componentInstance.findingId.set('fin_1');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.finding_id).toBe('fin_1');
    expect(stub.forRoute('/api/findings/fin_1', 'GET')).toHaveLength(1);
  });
});
