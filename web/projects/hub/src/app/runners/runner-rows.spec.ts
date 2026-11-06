import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { FRESHNESS_AGING_AFTER_MS } from 'fleet';
import {
  type RequestClientStub,
  runnerRegistryView,
  settle,
  stubError,
  stubHubClient,
  subscriptionUsageView,
} from 'fleet/testing';
import { injectRunnerRows } from './runner-rows';


@Component({ selector: 'app-test-runner-rows-host', template: `` })
class TestHost {
  readonly showRetired = signal(false);
  private readonly folded = injectRunnerRows(() => this.showRetired());
  readonly rows = this.folded.rows;
  readonly state = this.folded.state;
}

describe('injectRunnerRows subscription freshness fold', () => {
  const SAMPLED_AT = '2026-07-16T11:44:00.000Z';

  let stub: RequestClientStub;

  function runnersResponse(subscriptions: ReturnType<typeof subscriptionUsageView>[]) {
    return {
      runners: [runnerRegistryView({ runner_id: 'rn_tick', registered_at: SAMPLED_AT, last_seen_at: SAMPLED_AT, subscriptions })],
    };
  }

  afterEach(() => {
    stub.restore();
    vi.useRealTimers();
  });

  it("reclassifies a member's tier when the now signal advances, with no new query data", async () => {
    stub = stubHubClient({
      'GET /api/runners': () =>
        runnersResponse([subscriptionUsageView({ slug: 'anthropic', name: 'Anthropic', sampled_at: SAMPLED_AT })]),
      'GET /api/hosts': () => ({ hosts: [] }),
      'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
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
    stub = stubHubClient({
      'GET /api/runners': () =>
        runnersResponse([subscriptionUsageView({ slug: 'anthropic', name: 'Anthropic', sampled_at: 'not-a-date' })]),
      'GET /api/hosts': () => ({ hosts: [] }),
      'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
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
  const RETIRED = runnerRegistryView({
    runner_id: 'rn_gone',
    registered_at: AT,
    last_seen_at: AT,
    online: false,
    connection: 'offline',
    retired: true,
    retired_at: AT,
    retired_by: 'op',
  });
  const LIVE = { ...RETIRED, runner_name: 'rn_live', connection: 'online' as const, runner_id: 'rn_live', online: true, retired: false, retired_at: null, retired_by: null };

  let stub: RequestClientStub;

  beforeEach(async () => {
    // The hub hides a retired runner unless asked with `include_retired`.
    stub = stubHubClient({
      'GET /api/runners': (request) => ({ runners: request.search.includes('include_retired=true') ? [LIVE, RETIRED] : [LIVE] }),
      'GET /api/hosts': () => ({ hosts: [] }),
      'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
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

/** The `GET /api/hosts` handler a state case answers with. */
type HostsRoute = NonNullable<Parameters<typeof stubHubClient>[0]['GET /api/hosts']>;

describe('injectRunnerRows state', () => {
  let stub: RequestClientStub;
  let releaseHosts: () => void;

  async function configure(hosts: HostsRoute) {
    stub = stubHubClient({
      'GET /api/runners': () => ({ runners: [] }),
      'GET /api/hosts': hosts,
      'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  }

  afterEach(() => stub.restore());

  it('stays loading while the hosts read is pending, though the runners read has landed', async () => {
    const held = new Promise<void>((resolve) => (releaseHosts = resolve));
    await configure(async () => {
      await held;
      return { hosts: [] };
    });
    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    expect(fixture.componentInstance.state()).toBe('loading');

    releaseHosts();
    await settle(fixture);

    expect(fixture.componentInstance.state()).toBe('empty');
  });

  it('reads a failed hosts read as an error, not as an empty fleet', async () => {
    await configure(() => stubError(500, { detail: 'boom' }));
    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    expect(fixture.componentInstance.state()).toBe('error');
  });
});
