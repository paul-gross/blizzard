import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type ChunkSummary, FLEET_CLOCK } from 'fleet';
import { RUNNER_HOST_NAMES } from 'fleet/shell';
import {
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  backlogPeekEntry,
  chunkSummary,
  chunkUsageTotalView,
  decisionView,
  fleetSpendView,
  projectView,
  queuePeekEntry,
  questionView,
  runnerRegistryView,
  settle,
  stubError,
  stubHubClient,
  workItemEntry,
  workRefView,
} from 'fleet/testing';

import { vi } from 'vitest';

import { GlanceBoard } from './glance-board';

const NOW = Date.now();
const completedAt = (ageMs: number) => new Date(NOW - ageMs).toISOString();

/** The hub routes a spec declares — any subset of the hub's operations. */
type HubRoutes = Parameters<typeof stubHubClient>[0];

/**
 * `chunk-lanes.ts`'s lanes decide where in the list a chunk lands — this fixture
 * picks a chunk for every lane, plus terminal chunks outside the rolling window
 * that the list leaves out.
 */
const CHUNKS: ChunkSummary[] = [
  chunkSummary({
    chunk_id: 'ch_01needshuman0000000000000000',
    graph_id: 'gr_1',
    status: 'needs_human',
    current_node_id: 'nd_review',
    current_node_name: 'review',
    runner_id: 'r1',
  }),
  chunkSummary({
    chunk_id: 'ch_01waitinghuman00000000000000',
    graph_id: 'gr_1',
    status: 'waiting_on_human',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    runner_id: 'r2',
  }),
  chunkSummary({
    chunk_id: 'ch_01running000000000000000000',
    graph_id: 'gr_1',
    graph_name: 'default',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    runner_id: 'r3',
    runner_name: 'r-claude',
    project_id: 'pj_alpha',
    cost: chunkUsageTotalView({ cost_usd: 2.03, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 }),
  }),
  chunkSummary({
    chunk_id: 'ch_01readyfirst0000000000000000',
    graph_id: 'gr_1',
    status: 'ready',
    current_node_id: 'nd_test',
    current_node_name: 'test',
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01readysecond000000000000000',
    graph_id: 'gr_1',
    status: 'ready',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01donerecent0000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: completedAt(60 * 60 * 1000),
    current_node_id: null,
    runner_id: null,
    work_refs: [workRefView({ label: 'blizzard#79' })],
  }),
  chunkSummary({
    chunk_id: 'ch_01stoppedrecent00000000000000',
    graph_id: 'gr_1',
    status: 'stopped',
    terminal: true,
    completed_at: completedAt(2 * 60 * 60 * 1000),
    current_node_id: null,
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01doneold00000000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: completedAt(49 * 60 * 60 * 1000),
    current_node_id: null,
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01donemissing000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: null,
    current_node_id: null,
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01notready00000000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    runner_id: null,
  }),
  chunkSummary({
    chunk_id: 'ch_01notreadylater000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    runner_id: null,
  }),
];

// The backlog's hub order deliberately differs from the chunks list's order.
const BACKLOG = [
  backlogPeekEntry({ chunk_id: 'ch_01notreadylater000000000000', graph_id: 'gr_1', position: 0 }),
  backlogPeekEntry({ chunk_id: 'ch_01notready00000000000000000', graph_id: 'gr_1', position: 1 }),
];

const DECISIONS = [
  decisionView({
    decision_id: 'dc_1',
    chunk_id: 'ch_01needshuman0000000000000000',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-16T00:00:01Z',
    choices: [{ name: 'approve', description: '', requires_checks: false, would_override_red_checks: false }],
    check_results: [],
  }),
];

// Two projects, so the cards carry their project's colour on All.
const PROJECTS = {
  projects: [
    projectView({ project_id: 'pj_alpha', slug: 'alpha', name: 'Alpha', color: 'teal' }),
    projectView({ project_id: 'pj_beta', slug: 'beta', name: 'Beta', color: 'pink' }),
  ],
};

// The API's dispatch order deliberately differs from the chunks list's order.
const QUEUE = [
  queuePeekEntry({ chunk_id: 'ch_01readysecond000000000000000', graph_id: 'gr_1', position: 0 }),
  queuePeekEntry({ chunk_id: 'ch_01readyfirst0000000000000000', graph_id: 'gr_1', position: 1 }),
];

// The open ask names the waiting_on_human fixture above, whose card carries its question.
const QUESTIONS = [
  questionView({
    question_id: 'qn_01ask00000000000000000000000',
    chunk_id: 'ch_01waitinghuman00000000000000',
    runner_id: 'r2',
    question: '401 vs 403 for expired tokens?',
    options: [],
  }),
];

const RUNNERS = [
  runnerRegistryView({ runner_id: 'r1', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: true, connection: 'online' }),
  runnerRegistryView({ runner_id: 'r2', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: true, connection: 'online' }),
  runnerRegistryView({ runner_id: 'r3', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: false, connection: 'offline' }),
];

const SPEND = fleetSpendView({
  cost_usd: 18.4,
  cost_partial: false,
  input_tokens: 4_000_000,
  output_tokens: 2_000_000,
  cache_read_tokens: 100_000,
  cache_create_tokens: 0,
  since: '2026-07-20T00:00:00Z',
});

const HEALTH = { status: 'ok', service: 'blizzard-hub', version: '0.0.0', overridden: [] };

const ids = (el: HTMLElement): (string | null)[] =>
  [...el.querySelectorAll('[data-testid="glance-list"] > li')].map((li) => li.getAttribute('data-chunk') ?? li.getAttribute('data-testid'));

const card = (el: HTMLElement, chunkId: string): HTMLElement | null => el.querySelector(`[data-testid="glance-card"][data-chunk="${chunkId}"]`);

describe('GlanceBoard — one list and the vitals', () => {
  let stub: RequestClientStub;

  /** Answer the board's reads, `overrides` first. */
  function stubHub(overrides: HubRoutes = {}): void {
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: CHUNKS, next_cursor: null }),
      'GET /api/queue': () => ({ entries: QUEUE, next_cursor: null }),
      'GET /api/backlog': () => ({ entries: BACKLOG, next_cursor: null }),
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/questions': () => QUESTIONS,
      'GET /api/decisions': () => ({ decisions: DECISIONS }),
      'GET /api/projects': () => PROJECTS,
      'GET /api/runners': () => ({ runners: RUNNERS }),
      'GET /api/health': () => HEALTH,
      'GET /api/spend': () => SPEND,
      ...overrides,
    });
  }

  beforeEach(async () => {
    stubHub();
    await TestBed.configureTestingModule({
      imports: [GlanceBoard],
      providers: [
        provideZonelessChangeDetection(),
        // GlanceView's cards are `routerLink`s into the mobile chunk page.
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: RUNNER_HOST_NAMES, useValue: signal(new Map([['r3', 'atlas']])) },
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('lists needs, waiting, running, READY in queue order, the divider, BACKLOG in backlog order, the divider, then done in the last 48h', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);

    expect(ids(fixture.nativeElement as HTMLElement)).toEqual([
      'ch_01needshuman0000000000000000',
      'ch_01waitinghuman00000000000000',
      'ch_01running000000000000000000',
      'ch_01readysecond000000000000000',
      'ch_01readyfirst0000000000000000',
      'glance-ready-divider',
      'ch_01notreadylater000000000000',
      'ch_01notready00000000000000000',
      'glance-done-divider',
      'ch_01donerecent0000000000000000',
      'ch_01stoppedrecent00000000000000',
    ]);
  });

  it('keeps the backlog in the chunk read\'s order, unread, without queue:reorder', async () => {
    stub.restore();
    stubHub({ 'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [] }) });
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);

    const list = ids(fixture.nativeElement as HTMLElement);
    expect(list.slice(list.indexOf('glance-ready-divider') + 1, list.indexOf('glance-done-divider'))).toEqual(['ch_01notready00000000000000000', 'ch_01notreadylater000000000000']);
    expect(stub.forRoute('/api/backlog', 'GET')).toHaveLength(0);
  });

  it('carries an open ask on its chunk\'s card and pills an open gate', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(card(el, 'ch_01waitinghuman00000000000000')?.querySelector('[data-testid="glance-ask"]')?.textContent).toContain('401 vs 403 for expired tokens?');
    expect(card(el, 'ch_01needshuman0000000000000000')?.querySelector('.gate')).not.toBeNull();
    expect(card(el, 'ch_01waitinghuman00000000000000')?.querySelector('.gate')).toBeNull();
  });

  it('meta-lines a running card with its graph and node, its runner by host and name, and its spend, tinted by its project', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const running = card(fixture.nativeElement as HTMLElement, 'ch_01running000000000000000000');

    expect(running?.querySelector('.step')?.textContent?.trim()).toBe('default/build');
    expect(running?.querySelector('[data-testid="glance-runner"]')?.textContent?.replace(/\s+/g, '')).toBe('atlas/r-claude');
    expect(running?.querySelector('[data-testid="glance-cost"]')?.textContent).toContain('$2.03');
    expect(running?.classList).toContain('tinted');
  });

  it('words a stopped card\'s status, which its lane icon does not, and no other', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const words = [...(fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="glance-row-status"]')].map((el) =>
      el.closest('[data-chunk]')?.getAttribute('data-chunk'),
    );

    expect(words).toEqual(['ch_01stoppedrecent00000000000000']);
  });

  it('shows the list\'s error state when the chunks read fails', async () => {
    stub.restore();
    stubHub({ 'GET /api/chunks': () => stubError(503, { detail: 'unavailable' }) });
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="glance-list-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="glance-card"]')).toBeNull();
  });

  it('derives the vitals strip from the same list plus the runner registry', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const vital = (id: string) => el.querySelector(`[data-testid="${id}"] .v-n`)?.textContent?.trim();

    expect(vital('vital-needs-you')).toBe('2');
    expect(vital('vital-running')).toBe('1');
    expect(vital('vital-ready')).toBe('2');
    expect(vital('vital-done-today')).toBe('2');
    // r1 and r2 online, r3 offline — "2/3 runners up".
    expect(vital('vital-runners-up')).toBe('2/3');
  });

  it('leaves a retired runner out of the runners-up denominator', async () => {
    stub.restore();
    stubHub({ 'GET /api/runners': () => ({ runners: [...RUNNERS, { ...RUNNERS[2], runner_id: 'r4', retired: true }] }) });
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="vital-runners-up"]')?.textContent).toContain('2/3');
  });

  it('renders the fleet spend today as the session efficiency figure', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const parts = [...el.querySelectorAll('[data-testid="glance-spend-value"] .part')].map((p) => p.textContent?.trim());
    expect(parts).toEqual(['$18.40', '· 4.00MI', '/ 2.00MO', '× 97.6%']);
  });

  it('re-opens the spend-today read on the next day once the minute tick crosses midnight', async () => {
    let nowMs = new Date(2026, 6, 16, 23, 59, 30).getTime();
    TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => nowMs });
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      const fixture = TestBed.createComponent(GlanceBoard);
      await settle(fixture);
      const sinceOf = (day: Date): string => `since=${encodeURIComponent(day.toISOString())}`;
      const spendUrls = (): string[] => stub.forRoute('/api/spend', 'GET').map((r) => r.search);
      expect(spendUrls().some((u) => u.includes(sinceOf(new Date(2026, 6, 16))))).toBe(true);

      nowMs = new Date(2026, 6, 17, 0, 0, 30).getTime();
      vi.advanceTimersByTime(60_000);
      await settle(fixture);

      expect(spendUrls().some((u) => u.includes(sinceOf(new Date(2026, 6, 17))))).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

// A subscription-only fleet: no billed cost anywhere, only a runner-reported estimate —
// the glance board's combined figure reads `~$0.07` on its own rather than a plain
// billed $0.00.
const ESTIMATE_CHUNKS: ChunkSummary[] = [
  chunkSummary({
    chunk_id: 'ch_01estimaterun0000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    runner_id: 'r1',
    cost: chunkUsageTotalView({
      cost_usd: 0,
      estimated_cost_usd: 0.07,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
    }),
  }),
];

const ESTIMATE_SPEND = fleetSpendView({
  cost_usd: 0,
  cost_partial: false,
  estimated_cost_usd: 0.07,
  input_tokens: 900,
  output_tokens: 400,
  cache_read_tokens: 0,
  cache_create_tokens: 0,
  since: '2026-07-20T00:00:00Z',
});

describe('GlanceBoard — cost estimate, no billed cost', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: ESTIMATE_CHUNKS, next_cursor: null }),
      'GET /api/queue': () => ({ entries: [], next_cursor: null }),
      'GET /api/backlog': () => ({ entries: [], next_cursor: null }),
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/questions': () => [],
      'GET /api/decisions': () => ({ decisions: [] }),
      'GET /api/projects': () => ({ projects: [] }),
      'GET /api/runners': () => ({ runners: [] }),
      'GET /api/health': () => HEALTH,
      'GET /api/spend': () => ESTIMATE_SPEND,
    });
    await TestBed.configureTestingModule({
      imports: [GlanceBoard],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it("shows a card's own entirely-estimated figure as the estimate alone, marked ~", async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(card(el, 'ch_01estimaterun0000000000000')?.querySelector('[data-testid="glance-cost"]')?.textContent?.trim()).toBe('~$0.07');
  });

  it("shows the spend panel's own entirely-estimated figure as the estimate alone, marked ~", async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="glance-spend-value"] .part')?.textContent?.trim()).toBe('~$0.07');
  });
});

describe('GlanceBoard — work items', () => {
  let stub: RequestClientStub;
  const CHUNK_ID = 'ch_01workitems00000000000000000';

  beforeEach(async () => {
    stub = stubHubClient({
      'GET /api/chunks': () => ({
        chunks: [
          chunkSummary({
            chunk_id: CHUNK_ID,
            graph_id: 'gr_1',
            status: 'running',
            current_node_id: 'nd_build',
            current_node_name: 'build',
            runner_id: 'r1',
            work_refs: [workRefView({ source: 'wsrc_api', ref: '4', label: 'toy-api#4', web_url: null })],
          }),
        ],
        next_cursor: null,
      }),
      'GET /api/work-items': () => ({
        chunks: {
          [CHUNK_ID]: [
            workItemEntry({ source: 'wsrc_api', ref: '4', label: 'toy-api#4', title: 'Add idempotency keys to POST /payments', web_url: null }),
          ],
        },
      }),
      'GET /api/queue': () => ({ entries: [], next_cursor: null }),
      'GET /api/backlog': () => ({ entries: [], next_cursor: null }),
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/runners': () => ({ runners: [] }),
      'GET /api/questions': () => [],
      'GET /api/decisions': () => ({ decisions: [] }),
      'GET /api/projects': () => ({ projects: [] }),
      'GET /api/health': () => HEALTH,
      'GET /api/spend': () => SPEND,
    });
    await TestBed.configureTestingModule({
      imports: [GlanceBoard],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it("heads a card with its work item's label and short id, its title read in under them", async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const row = card(fixture.nativeElement as HTMLElement, CHUNK_ID);

    expect(row?.querySelector('.head [data-testid="glance-work-ref"]')?.textContent?.trim()).toBe('toy-api#4');
    expect(row?.querySelector('.head .cid')?.textContent?.trim()).toMatch(/^C-/);
    expect(row?.querySelector('[data-testid="glance-title"]')?.textContent?.trim()).toBe('Add idempotency keys to POST /payments');
    expect(row?.querySelector('[data-testid="glance-title-skeleton"]')).toBeNull();
    expect(stub.forRoute('/api/work-items', 'GET')).toHaveLength(1);
  });
});
