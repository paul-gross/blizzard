import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { injectHubFleetSpendQuery } from './fleet-spend.query';

const SPEND_BODY = {
  since: '2026-07-16T00:00:00+00:00',
  until: null,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_create_tokens: 0,
  cost_usd: 0,
  cost_partial: false,
};

/** A fetch stub capturing each request's full URL (query string included) — unlike
 * the shared `stubRequestClient` helper, which drops the query string, this spec needs
 * it to prove `until` reaches the request when given and is omitted when not. */
function stubFetchCapturingUrl(): { urls: string[]; restore: () => void } {
  const urls: string[] = [];
  const previousFetch = globalThis.fetch;
  const fakeFetch = async (input: Request): Promise<Response> => {
    urls.push(input.url);
    return new Response(JSON.stringify(SPEND_BODY), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
  return {
    urls,
    restore: () => hubClient.setConfig({ baseUrl: '', fetch: previousFetch }),
  };
}

@Component({
  selector: 'fleet-test-spend-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestSpendQueryHost {
  readonly since = signal('2026-07-16T00:00:00+00:00');
  readonly until = signal<string | undefined>(undefined);
  readonly query = injectHubFleetSpendQuery(
    () => this.since(),
    () => this.until(),
  );
}

describe('injectHubFleetSpendQuery (issue #183)', () => {
  let stub: { urls: string[]; restore: () => void };
  afterEach(() => stub?.restore());

  it('omits until from the request when the accessor returns undefined', async () => {
    stub = stubFetchCapturingUrl();
    TestBed.configureTestingModule({
      imports: [TestSpendQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestSpendQueryHost);
    await settle(fixture);

    expect(stub.urls).toHaveLength(1);
    expect(stub.urls[0]).not.toContain('until=');
  });

  it('forwards until to the request when given', async () => {
    stub = stubFetchCapturingUrl();
    TestBed.configureTestingModule({
      imports: [TestSpendQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestSpendQueryHost);
    fixture.componentInstance.until.set('2026-07-17T00:00:00+00:00');
    await settle(fixture);

    expect(stub.urls).toHaveLength(1);
    expect(stub.urls[0]).toContain('until=2026-07-17T00%3A00%3A00%2B00%3A00');
  });

  it('gives two windows sharing a since but differing in until distinct cache entries', async () => {
    stub = stubFetchCapturingUrl();
    const queryClient = new QueryClient();
    TestBed.configureTestingModule({
      imports: [TestSpendQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });

    const today = TestBed.createComponent(TestSpendQueryHost);
    await settle(today);

    const yesterday = TestBed.createComponent(TestSpendQueryHost);
    yesterday.componentInstance.until.set('2026-07-16T00:00:00+00:00');
    await settle(yesterday);

    // The key assertion: two distinct cache entries. `staleTime` defaults to 0, so
    // a `stub.urls` fetch-count assertion alone would pass even on a colliding key
    // — a stale-on-arrival query refetches on a second mount regardless of whether
    // it shares a cache entry with the first (that fetch-count check still holds
    // below, but only as a secondary signal).
    expect(queryClient.getQueryCache().getAll()).toHaveLength(2);
    expect(stub.urls).toHaveLength(2);
  });
});
