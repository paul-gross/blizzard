import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { toneColor } from '../kit/kit-badge';
import { OPERATOR_ME_RESPONSE } from '../testing/auth-fixtures';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { RunnerPanel } from './runner-panel';

/** A `contributor`'s `/api/me` — every day-to-day operating permission, but not the
 * admin-tier `runner:pause` (#93). Drives the brake-gating assertion. */
const CONTRIBUTOR_ME = {
  ...OPERATOR_ME_RESPONSE,
  role: 'contributor',
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'runner:pause' && p !== 'graph:edit' && p !== 'user:manage'),
};

const NOW = new Date().toISOString();
// One runner per pause state: none, the fleet's brake, the runner's own, and both
// (blizzard#43 — they are separate concepts and the strip must say which).
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
// the pair the claim-status tone assertion (#156) reads, where the node alone would
// make the two lines identical.
//
// The done row documents the shape the hub sends (issue #140): a terminal chunk reports
// `runner_id: null` / `environment_count: 0` even when its route facts still name the
// runner that worked it. It is a fixture, NOT a regression guard — the panel reaches it
// on the same unrouted branch `ch_01idle…` already covers, so removing it fails nothing.
// The behavior it documents lives wholly in the hub and is pinned there, by
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

describe('RunnerPanel', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      if (path === '/api/runners/rn_online/pause') return RUNNERS.runners[0];
      if (path === '/api/runners/rn_paused/resume') return RUNNERS.runners[1];
      if (path === '/api/runners/rn_local/pause') return RUNNERS.runners[2];
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders each runner with its liveness and paused state', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(5);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');

    // Each row lists the chunks that runner holds a route on — short name + node —
    // and only its own: rn_online holds one claim, at build.
    const claims = el.querySelectorAll('[data-runner="rn_online"] [data-testid="runner-claim"]');
    expect(claims).toHaveLength(1);
    expect(claims[0].textContent).toContain('build');
    expect(el.querySelectorAll('[data-runner="rn_local"] [data-testid="runner-claim"]')).toHaveLength(0);
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it("ends each claim with the chunk's status, toned off the board's own ladder (#156)", async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const badge = (id: string) =>
      el.querySelector<HTMLElement>(`[data-runner="${id}"] [data-testid="runner-claim-status"] .badge`);

    // Both chunks sit at `build`; only the status tells them apart, which is the point.
    // The colors come from STATUS_TONE → toneColor, not a mapping of this panel's own, so
    // a claim always reads the same tone as that chunk's board card.
    expect(badge('rn_online')?.textContent?.trim()).toBe('running');
    expect(badge('rn_online')?.getAttribute('style')).toContain(toneColor('running'));
    expect(badge('rn_both')?.textContent?.trim()).toBe('needs_human');
    expect(badge('rn_both')?.getAttribute('style')).toContain(toneColor('needs'));
  });

  it('renders a slot bar from env_capacity and summed environment_count, omitting it when null (#69)', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // rn_online: 4-slot capacity, one chunk holding 2 envs → a 4-cell bar, 2 filled.
    const bar = el.querySelector('[data-runner="rn_online"] [data-testid="runner-slot-bar"]');
    expect(bar).not.toBeNull();
    expect(bar?.querySelectorAll('.cell')).toHaveLength(4);
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(bar?.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 slots');

    // rn_local's capacity is null — no bar rather than a zero-slot one.
    expect(el.querySelector('[data-runner="rn_local"] [data-testid="runner-slot-bar"]')).toBeNull();
  });

  it('distinguishes the hub brake, the runner\'s own, and both (#43)', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const badges = (id: string) => ({
      hub: el.querySelector(`[data-runner="${id}"] [data-testid="runner-hub-paused"]`) !== null,
      local: el.querySelector(`[data-runner="${id}"] [data-testid="runner-locally-paused"]`) !== null,
    });

    expect(badges('rn_online')).toEqual({ hub: false, local: false });
    expect(badges('rn_paused')).toEqual({ hub: true, local: false });
    expect(badges('rn_local')).toEqual({ hub: false, local: true });
    expect(badges('rn_both')).toEqual({ hub: true, local: true }); // both, not one collapsed badge
  });

  it("names a spend-ceiling escalation's reason on the locally-paused badge (#61)", async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const title = (id: string) =>
      el.querySelector(`[data-runner="${id}"] [data-testid="runner-locally-paused"]`)?.getAttribute('title');

    expect(title('rn_ceiling')).toBe(CEILING_REASON);
    // A manual pause carries no reason — the badge falls back to the generic hint rather
    // than showing nothing or a stale reason.
    expect(title('rn_local')).toBe('This runner paused itself. Clear it on the runner: blizzard runner start');
  });

  it('offers to pause a locally-paused runner at the hub — the board cannot clear its own brake', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // rn_local stopped itself, but the hub has not paused it: the only thing this button
    // can do is add the hub's brake, so it must not read "Resume".
    const button = el.querySelector<HTMLButtonElement>('[data-runner="rn_local"] [data-testid="runner-toggle"]');
    expect(button?.textContent?.trim()).toBe('Pause');
  });

  it('pauses an online runner via the client call', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-runner="rn_online"] [data-testid="runner-toggle"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/runners/rn_online/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/runners/rn_online/resume', 'POST')).toHaveLength(0);
  });

  it('resumes a paused runner via the client call', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-runner="rn_paused"] [data-testid="runner-toggle"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/runners/rn_paused/resume', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/runners/rn_paused/pause', 'POST')).toHaveLength(0);
  });

  it('withholds the hub brake for a contributor (no runner:pause, #93) — the registry still renders', async () => {
    // The adjudication case: `runner:pause` is admin-tier, so a contributor who reaches
    // the board must not be offered the brake it could only 403 on. The registry and its
    // paused badges (a `fleet:view` read) stay; only the toggle button is withheld.
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return CONTRIBUTOR_ME;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      return {};
    });
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(5);
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelectorAll('[data-testid="runner-toggle"]')).toHaveLength(0);
  });

  /*
   * Part A conformance: one `pauseMutation` instance fires once per toggled row, so
   * "disabled while pending" must scope to the row whose own mutation is in flight —
   * a sibling row's toggle must stay clickable.
   */
  describe('pause/resume per-row pending scope (Part A)', () => {
    const toggle = (el: HTMLElement, id: string): HTMLButtonElement | null =>
      el.querySelector<HTMLButtonElement>(`[data-runner="${id}"] [data-testid="runner-toggle"]`);

    it("disables only the clicked row's toggle while its mutation is pending, re-enabling once it settles", async () => {
      const fixture = TestBed.createComponent(RunnerPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );

      toggle(el, 'rn_online')?.click();
      // Held open by the `invalidateQueries` spy above — `settle()`'s own
      // `whenStable()` would hang on it, so a bare macrotask tick + a manual
      // `detectChanges()` stands in, the same idiom `status.query.spec.ts` uses
      // for the same reason.
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();

      // The clicked row disables…
      expect(toggle(el, 'rn_online')?.disabled).toBe(true);
      // …but a sibling row's own toggle stays clickable — the two fire the same
      // `pauseMutation` instance with different variables.
      expect(toggle(el, 'rn_paused')?.disabled).toBe(false);

      resolveInvalidate();
      await settle(fixture);

      expect(toggle(el, 'rn_online')?.disabled).toBe(false);
    });

    // --- Pending hub_paused override (`bzh:frontend-pending-override`) -------------
    //
    // The hub brake is a plain fact this exact mutation sets directly
    // (`domain/execution/pause.md`'s "each cleared only where it was set") — the
    // mutation's own variables already name the requested value, so the override is
    // total and needs no guessing.

    it("renders the requested hub_paused while pausing rn_online is pending — a sibling row's own paused state is unaffected", async () => {
      const fixture = TestBed.createComponent(RunnerPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );

      toggle(el, 'rn_online')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();

      // The clicked row renders paused ahead of the server confirming it…
      expect(el.querySelector('[data-runner="rn_online"] [data-testid="runner-hub-paused"]')).not.toBeNull();
      expect(toggle(el, 'rn_online')?.textContent?.trim()).toBe('Resume');
      // …while a sibling row's own real hub_paused (already true, and not the one
      // clicked) is untouched.
      expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
      expect(toggle(el, 'rn_paused')?.textContent?.trim()).toBe('Resume');

      resolveInvalidate();
      await settle(fixture);
    });

    it('reverts the hub_paused override to the real value on a rejected pause', async () => {
      stub.restore();
      stub = stubRequestClient(hubClient, (method, path) => {
        if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
        if (method === 'GET' && path === '/api/runners') return RUNNERS;
        if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
        if (path === '/api/runners/rn_online/pause') return stubError(409, { detail: 'runner already paused' });
        return {};
      });
      const fixture = TestBed.createComponent(RunnerPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );

      toggle(el, 'rn_online')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();

      expect(el.querySelector('[data-runner="rn_online"] [data-testid="runner-hub-paused"]')).not.toBeNull();

      resolveInvalidate();
      await settle(fixture);

      // Never a cache write, so the rejection needs nothing special to revert — the
      // moment `isPending()` clears, the row falls back to the real (still-false)
      // `hub_paused` on its own.
      expect(el.querySelector('[data-runner="rn_online"] [data-testid="runner-hub-paused"]')).toBeNull();
      expect(toggle(el, 'rn_online')?.textContent?.trim()).toBe('Pause');
    });

    it("reports a pause failure through the panel's visible error slot", async () => {
      stub.restore();
      stub = stubRequestClient(hubClient, (method, path) => {
        if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
        if (method === 'GET' && path === '/api/runners') return RUNNERS;
        if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
        if (path === '/api/runners/rn_online/pause') return stubError(409, { detail: 'runner already paused' });
        return {};
      });
      const fixture = TestBed.createComponent(RunnerPanel);
      await settle(fixture);
      const el = fixture.nativeElement as HTMLElement;

      toggle(el, 'rn_online')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="runner-action-error"]')?.textContent).toBe('runner already paused');
      // The failure never disables a sibling row that never fired the mutation.
      expect(toggle(el, 'rn_paused')?.disabled).toBe(false);
    });
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

describe('RunnerPanel external-subscription pace bars (issue #218)', () => {
  let stub: RequestClientStub;

  const RUNNERS_WITH_USAGE = {
    runners: [
      runner('rn_paced', {
        external_subscription_usage: {
          sampled_at: NOW,
          windows: [
            { window: '5h', utilization_pct: 40, resets_at: '2026-07-16T17:00:00.000Z', window_seconds: 5 * 60 * 60 },
            { window: '7d', utilization_pct: 70, resets_at: '2026-07-22T12:00:00.000Z', window_seconds: 7 * 24 * 60 * 60 },
          ],
        },
      }),
      runner('rn_unsampled', { external_subscription_usage: null }),
    ],
  };

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return RUNNERS_WITH_USAGE;
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('renders one pace bar per sampled window, and none for a runner that has never sampled', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const bars = el.querySelectorAll('[data-runner-pace-bar="rn_paced"]');
    expect(bars).toHaveLength(2);
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);
    expect(el.querySelector('[data-runner="rn_unsampled"] [data-testid="runner-pace-bars"]')).toBeNull();
  });
});

describe('RunnerPanel per-subscription data model (blizzard#436)', () => {
  // Data model only — blizzard#478 owns rendering it, so these assert on the
  // component's `rows` output directly rather than on any DOM the template does not
  // yet grow.
  let stub: RequestClientStub;

  const RUNNERS_WITH_SUBSCRIPTIONS = {
    runners: [
      runner('rn_multi', {
        // Both subscriptions report a "5h" window — grouping by slug is what keeps
        // them from colliding into one bar list.
        external_subscription_usage: {
          sampled_at: NOW,
          windows: [{ window: '5h', utilization_pct: 40, resets_at: '2026-07-16T17:00:00.000Z', window_seconds: 5 * 60 * 60 }],
        },
        subscriptions: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            sampled_at: NOW,
            windows: [{ window: '5h', utilization_pct: 40, resets_at: '2026-07-16T17:00:00.000Z', window_seconds: 5 * 60 * 60 }],
          },
          {
            slug: 'anthropic-secondary',
            name: 'Anthropic (secondary)',
            sampled_at: NOW,
            windows: [{ window: '5h', utilization_pct: 90, resets_at: '2026-07-16T18:00:00.000Z', window_seconds: 5 * 60 * 60 }],
          },
        ],
      }),
      // Legacy-only: reports the single-subscription field but no `subscriptions`
      // collection at all — an unupgraded hub, or a runner with no declarations.
      runner('rn_legacy', {
        external_subscription_usage: {
          sampled_at: NOW,
          windows: [{ window: '5h', utilization_pct: 55, resets_at: '2026-07-16T17:00:00.000Z', window_seconds: 5 * 60 * 60 }],
        },
      }),
    ],
  };

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return RUNNERS_WITH_SUBSCRIPTIONS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [RunnerPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('keeps two subscriptions sharing an identical window label distinct, grouped by slug', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const rows = fixture.componentInstance['rows']();
    const row = rows.find((r) => r.runner_id === 'rn_multi');

    expect(row?.subscriptionPaces).toHaveLength(2);
    const bySlug = new Map(row?.subscriptionPaces.map((s) => [s.slug, s]));
    expect(bySlug.get('anthropic-default')?.name).toBe('Anthropic (default)');
    expect(bySlug.get('anthropic-default')?.paceBars).toEqual([expect.objectContaining({ window: '5h', utilizationPct: 40 })]);
    expect(bySlug.get('anthropic-secondary')?.paceBars).toEqual([expect.objectContaining({ window: '5h', utilizationPct: 90 })]);
  });

  it('still yields the legacy paceBars for a runner reporting no subscriptions collection', async () => {
    const fixture = TestBed.createComponent(RunnerPanel);
    await settle(fixture);
    const rows = fixture.componentInstance['rows']();
    const row = rows.find((r) => r.runner_id === 'rn_legacy');

    expect(row?.subscriptionPaces).toEqual([]);
    expect(row?.paceBars).toEqual([expect.objectContaining({ window: '5h', utilizationPct: 55 })]);
  });
});
