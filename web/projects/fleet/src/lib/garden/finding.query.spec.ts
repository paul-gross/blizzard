import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectHubFindingsBucketQuery, injectHubFindingsQuery } from './finding.query';

@Component({
  selector: 'fleet-test-findings-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestFindingsQueryHost {
  readonly findingIds = signal<readonly string[]>([]);
  readonly query = injectHubFindingsQuery(() => this.findingIds());
}

/** A fetch stub capturing each request's full URL (query string included) —
 * `fleet-spend.query.spec.ts`'s own local helper, since the shared `stubRequestClient`
 * drops the query string and this spec needs it to prove `include_gone` rides the
 * request. */
function stubFetchCapturingUrl(body: unknown): { urls: string[]; restore: () => void } {
  const urls: string[] = [];
  const previousFetch = globalThis.fetch;
  const fakeFetch = async (input: Request): Promise<Response> => {
    urls.push(input.url);
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
  return {
    urls,
    restore: () => hubClient.setConfig({ baseUrl: '', fetch: previousFetch }),
  };
}

@Component({
  selector: 'fleet-test-findings-bucket-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestFindingsBucketQueryHost {
  readonly routine = signal<string | null>(null);
  readonly scope = signal<string | null>(null);
  readonly query = injectHubFindingsBucketQuery(
    () => this.routine(),
    () => this.scope(),
  );
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

describe('injectHubFindingsBucketQuery', () => {
  let stub: { urls: string[]; restore: () => void };
  afterEach(() => stub?.restore());

  it('stays disabled until both routine and scope are set', async () => {
    stub = stubFetchCapturingUrl([]);
    TestBed.configureTestingModule({
      imports: [TestFindingsBucketQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingsBucketQueryHost);
    fixture.componentInstance.routine.set('comments');
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.urls).toHaveLength(0);
  });

  it('reads the bucket off GET /api/findings?routine=&scope=&include_gone=true', async () => {
    stub = stubFetchCapturingUrl([
      {
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
      },
    ]);
    TestBed.configureTestingModule({
      imports: [TestFindingsBucketQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestFindingsBucketQueryHost);
    fixture.componentInstance.routine.set('comments');
    fixture.componentInstance.scope.set('blizzard');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.map((f) => f.finding_id)).toEqual(['fin_1']);
    expect(stub.urls).toHaveLength(1);
    const url = new URL(stub.urls[0]);
    expect(url.pathname).toBe('/api/findings');
    expect(url.searchParams.get('routine')).toBe('comments');
    expect(url.searchParams.get('scope')).toBe('blizzard');
    expect(url.searchParams.get('include_gone')).toBe('true');
  });
});
