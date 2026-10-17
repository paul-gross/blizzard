import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';

import { settle, OPERATOR_ME_RESPONSE, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { hubClient } from 'fleet';
import { RunnerPanel } from './runner-panel';

/** Every permission a runner action could be gated on — the rail must still offer none. */
const ALL_PERMISSIONS_ME = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:pause' as const, 'runner:retire' as const],
};

const NOW = new Date().toISOString();
// One runner per pause state: none, the fleet's brake, the runner's own, and both
// (they are separate concepts and the strip must say which).
const runner = (id: string, over: Partial<Record<string, unknown>> = {}) => ({
  runner_id: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  ...over,
});
const CEILING_REASON = 'spend ceiling $5.00 reached over the trailing 24h (spend $7.00)';
const RUNNERS = {
  runners: [
    // rn_online reports a 4-slot pool (#69); rn_local's capacity is null — an older client
    // that predates the field — so its row renders no slot bar.
    runner('rn_online', { env_capacity: 4 }),
    runner('rn_paused', { hub_paused: true }),
    runner('rn_local', { locally_paused: true, env_capacity: null }),
    runner('rn_both', { hub_paused: true, locally_paused: true }),
    runner('rn_ceiling', {
      locally_paused: true,
      locally_paused_by: 'runner-ceiling',
      locally_paused_reason: CEILING_REASON,
    }),
  ],
};

// The board's chunk list, as the claims read consumes it: one chunk routed to
// rn_online at build, one routed elsewhere, one escalated at rn_both, one unrouted,
// and one rn_online FINISHED — only the first shows under rn_online.
//
// rn_both's chunk sits at the same node as rn_online's and differs only in status:
// the pair the claim-status tone assertion reads, where the node alone would
// make the two lines identical.
//
// The done row (`runner_id: null` / `environment_count: 0`) takes the same unrouted
// branch `ch_01idle…` covers; the hub's terminal-chunk shape is pinned by
// `tests/test_route_claim.py::test_summary_reports_a_finished_chunk_as_unrouted`.
const CHUNKS = [
  {
    chunk_id: 'ch_01claim000000000000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'rn_online',
    environment_count: 2, // a grouped route holding two envs — the slot bar's numerator
  },
  {
    chunk_id: 'ch_01other000000000000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_review',
    current_node_name: 'review',
    model: 'claude-opus-4-8',
    runner_id: 'rn_paused',
  },
  {
    chunk_id: 'ch_01needs000000000000000000000',
    graph_id: 'gr_1',
    status: 'needs_human',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'rn_both',
    environment_count: 1,
  },
  {
    chunk_id: 'ch_01idle0000000000000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01done0000000000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    current_node_id: null,
    current_node_name: null,
    model: 'claude-opus-4-8',
    runner_id: null, // finished at rn_online; the hub reports it unrouted (#140)
    environment_count: 0,
  },
];


const PROVIDERS = () => [
  provideZonelessChangeDetection(),
  provideRouter([]),
  provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
];

describe('RunnerPanel', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return ALL_PERMISSIONS_ME;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({ imports: [RunnerPanel], providers: PROVIDERS() }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders each runner as one compact row with its liveness and paused state', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(5);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_local"] [data-testid="runner-locally-paused"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_online"] [data-testid="runner-hub-paused"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_both"] [data-testid="runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_both"] [data-testid="runner-locally-paused"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_ceiling"] [data-testid="runner-locally-paused"]')?.getAttribute('title')).toBe(
      CEILING_REASON,
    );
  });

  it('draws one env strip for the fleet from the host-less runners that report a pool', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const strip = el.querySelector('[data-testid="runner-env-strip"]');
    expect(strip?.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 envs');
    expect(el.querySelectorAll('[data-testid="runner-env-strip"]')).toHaveLength(1);
  });

  it('offers no action to an identity holding every permission, only a link to the Fleet tab', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-kit-button')).toBeNull();
    for (const testid of ['runner-release', 'host-enroll', 'host-revoke-token', 'host-retire', 'runner-toggle', 'host-card']) {
      expect(el.querySelector(`[data-testid="${testid}"]`), testid).toBeNull();
    }
    expect(el.querySelector('[data-testid="runner-fleet-link"]')?.getAttribute('href')).toBe('/fleet');
  });
});

describe('RunnerPanel seenLabel (bzh:utc-instants)', () => {
  // Liveness is decided on the hub's clock (`online`); this label is decoration
  // computed against the browser's clock, so `Date.now()` is pinned rather than the
  // wall clock, and `last_seen_at` is placed relative to it.
  const REF = Date.parse('2026-07-16T12:00:00.000Z');
  let stub: RequestClientStub;

  function render(lastSeenAt: string, online: boolean): Promise<HTMLElement> {
    const runners = {
      runners: [
        { runner_id: 'r1', workspace_id: 'ws_a', registered_at: lastSeenAt, last_seen_at: lastSeenAt, online, paused: false },
      ],
    };
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return runners;
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    return TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    })
      .compileComponents()
      .then(async () => {
        const fixture = TestBed.createComponent(RunnerPanel);
        await settle(fixture);
        return fixture.nativeElement as HTMLElement;
      });
  }

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    stub.restore();
  });

  it('reads a fresh heartbeat as "seen Ns ago"', async () => {
    const el = await render('2026-07-16T11:59:55.000Z', true); // 5s before REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 5s ago');
  });

  it('reads a small browser-vs-hub skew (<=60s in the future) as "seen 0s ago"', async () => {
    const el = await render('2026-07-16T12:00:30.000Z', true); // 30s after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 0s ago');
  });

  it('still reads "seen 0s ago" at exactly the 60s tolerance boundary', async () => {
    const el = await render('2026-07-16T12:01:00.000Z', true); // 60s after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 0s ago');
  });

  it('does not render a confident 0s for a stamp hours in the future — falls through to online', async () => {
    // The naive-timestamp bug this guards against: a naive wire stamp on a UTC-5 box
    // reads five hours ahead of the true instant (bzh:utc-instants).
    const el = await render('2026-07-16T17:00:00.000Z', true); // 5h after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('online');
  });

  it('falls through to offline (not "0s ago") for a stale runner behind a stamp hours in the future', async () => {
    const el = await render('2026-07-16T17:00:00.000Z', false); // 5h after REF
    expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('offline');
  });
});


describe('RunnerPanel host cards', () => {
  let stub: RequestClientStub;

  const HOSTED = {
    runners: [
      runner('rA', {
        runner_name: 'alpha',
        host_id: 'h1',
        projects: [{ entry: 'default', project_id: 'pj_default', slug: 'default', name: 'Default' }],
        bindings: [
          { project: 'default', workspace_id: 'ws-1', agents: 2 },
          { project: 'default', workspace_id: 'ws-9', agents: 1, workspace_unreported: true },
        ],
      }),
      runner('rB', { runner_name: 'bravo', host_id: 'h1' }),
      runner('rC', { runner_name: 'charlie' }),
    ],
  };
  const HOSTS = {
    hosts: [
      {
        host_id: 'h1',
        host_name: 'build-box',
        connection: 'online',
        online: true,
        last_seen_at: NOW,
        workspaces: [{ workspace_id: 'ws-1', env_capacity: 3 }, { workspace_id: 'ws-2' }],
        subscriptions: [],
        subscription_usage: [],
      },
    ],
  };
  const LIVE = [{ chunk_id: 'ch_live', graph_id: 'gr_1', status: 'running', runner_id: 'rA', project_id: 'pj_default', environment_count: 2 }];

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return HOSTED;
      if (method === 'GET' && path === '/api/hosts') return HOSTS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: LIVE, next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({ imports: [RunnerPanel], providers: PROVIDERS() }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('folds the hosts, runners and live chunks reads into one strip and one row per runner, no host cards', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="host-card"]')).toBeNull();
    expect([...el.querySelectorAll('[data-testid="runner"]')].map((li) => li.getAttribute('data-runner'))).toEqual(['rA', 'rB', 'rC']);
    expect(el.querySelector('[data-testid="runner-env-strip"] [data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/3 envs');
    expect(el.querySelector('[data-runner="rA"] [data-testid="runner-agents"] [data-testid="slot-bar-label"]')?.textContent?.trim()).toBe(
      '1/3 agents',
    );
    expect(el.querySelector('[data-runner="rB"] [data-testid="runner-agents"]')).toBeNull();
  });
});
