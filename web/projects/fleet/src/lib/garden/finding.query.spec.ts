import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectHubFindingsQuery } from './finding.query';

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

  it("drops a failed id's row rather than failing the whole join", async () => {
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
      if (method === 'GET' && path === '/api/findings/fin_2') return stubError(404, { detail: 'not found' });
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
    expect(data?.map((f) => f.finding_id)).toEqual(['fin_1']);
    expect(fixture.componentInstance.query.isError()).toBe(false);
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
