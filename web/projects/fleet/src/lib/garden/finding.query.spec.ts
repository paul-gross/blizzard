import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectHubFindingQuery, injectHubFindingsQuery } from './finding.query';

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

@Component({
  selector: 'fleet-test-findings-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestFindingsQueryHost {
  readonly findingIds = signal<readonly string[]>([]);
  readonly query = injectHubFindingsQuery(() => this.findingIds());
}

describe('injectHubFindingsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads every id independently, live, and joins the results', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/findings/fin_1') {
        return {
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
      }
      if (method === 'GET' && path === '/api/findings/fin_2') {
        return {
          finding_id: 'fin_2',
          routine_name: 'comments',
          scope_slug: 'blizzard',
          class: 'stale-docstring',
          locus: 'src/b.py:9',
          summary: 'b',
          state: 'live',
          live: true,
          observed_count: 1,
          last_seen_at: '2026-01-01T00:00:00Z',
        };
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestFindingsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingsQueryHost);
    fixture.componentInstance.findingIds.set(['fin_1', 'fin_2']);
    await settle(fixture);

    const data = fixture.componentInstance.query.data();
    expect(data?.map((f) => f.finding_id).sort()).toEqual(['fin_1', 'fin_2']);
    expect(stub.forRoute('/api/findings/fin_1', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/findings/fin_2', 'GET')).toHaveLength(1);
  });

  it('stays disabled for an empty id list', async () => {
    stub = stubRequestClient(hubClient, () => ({}));
    TestBed.configureTestingModule({
      imports: [TestFindingsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.forRoute('/api/findings/fin_1', 'GET')).toHaveLength(0);
  });
});
