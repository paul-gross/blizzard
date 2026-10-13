import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { FRESHNESS_AGING_AFTER_MS, hubClient } from 'fleet';
import { settle, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { injectRunnerRows } from './runner-rows';


@Component({ selector: 'app-test-runner-rows-host', template: `` })
class TestHost {
  readonly showRetired = signal(false);
  readonly rows = injectRunnerRows(() => this.showRetired()).rows;
}

describe('injectRunnerRows subscription freshness fold', () => {
  const SAMPLED_AT = '2026-07-16T11:44:00.000Z';

  let stub: RequestClientStub;

  function runnersResponse(subscriptions: readonly Record<string, unknown>[]) {
    return {
      runners: [
        {
          runner_id: 'rn_tick',
          workspace_id: 'ws_a',
          registered_at: SAMPLED_AT,
          last_seen_at: SAMPLED_AT,
          online: true,
          hub_paused: false,
          locally_paused: false,
          subscriptions,
        },
      ],
    };
  }

  afterEach(() => {
    stub.restore();
    vi.useRealTimers();
  });

  it("reclassifies a member's tier when the now signal advances, with no new query data", async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/runners') {
        return runnersResponse([
          { slug: 'anthropic', name: 'Anthropic', sampled_at: SAMPLED_AT, windows: [], condition: null },
        ]);
      }
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();

    // Only Date/setInterval are faked — `settle`'s own real `setTimeout` still runs, so
    // the query's stubbed fetch keeps resolving normally.
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(Date.parse(SAMPLED_AT));

    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);
    expect(fixture.componentInstance.rows()[0]?.subscriptionPaces[0]?.freshness).toBe('fresh');

    vi.setSystemTime(Date.parse(SAMPLED_AT) + FRESHNESS_AGING_AFTER_MS + 60_000);
    vi.advanceTimersByTime(30_000); // injectRunnerRows' own now-signal tick period
    fixture.detectChanges();

    expect(fixture.componentInstance.rows()[0]?.subscriptionPaces[0]?.freshness).toBe('aging');
  });

  it('gives no tier and no refreshed label for an unparseable sampled_at', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/runners') {
        return runnersResponse([
          { slug: 'anthropic', name: 'Anthropic', sampled_at: 'not-a-date', windows: [], condition: null },
        ]);
      }
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    const member = fixture.componentInstance.rows()[0]?.subscriptionPaces[0];
    expect(member?.freshness).toBeNull();
    expect(member?.refreshedLabel).toBeNull();
  });
});

describe('injectRunnerRows retired runners', () => {
  const AT = '2026-07-16T11:44:00.000Z';
  const RETIRED = {
    runner_id: 'rn_gone',
    workspace_id: 'ws_a',
    registered_at: AT,
    last_seen_at: AT,
    online: false,
    hub_paused: false,
    locally_paused: false,
    retired: true,
    retired_at: AT,
    retired_by: 'op',
  };
  const LIVE = { ...RETIRED, runner_id: 'rn_live', online: true, retired: false, retired_at: null, retired_by: null };

  let stub: RequestClientStub;

  beforeEach(async () => {
    // The hub hides a retired runner unless asked with `include_retired`.
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/runners') {
        const asked = stub.forRoute('/api/runners').at(-1)?.search.includes('include_retired=true');
        return { runners: asked ? [LIVE, RETIRED] : [LIVE] };
      }
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('excludes retired runners by default, without asking the hub for them', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    expect(fixture.componentInstance.rows().map((r) => r.runner_id)).toEqual(['rn_live']);
    expect(stub.forRoute('/api/runners').every((r) => !r.search.includes('include_retired'))).toBe(true);
  });

  it('shows retired runners, marked with when and by whom, once included', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    fixture.componentInstance.showRetired.set(true);
    await settle(fixture);

    const rows = fixture.componentInstance.rows();
    expect(rows.map((r) => r.runner_id)).toEqual(['rn_live', 'rn_gone']);
    expect(rows[1]).toMatchObject({ retired: true, retired_at: AT, retired_by: 'op' });
  });
});
