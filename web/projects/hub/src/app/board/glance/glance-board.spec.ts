import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { FLEET_CLOCK, hubClient } from 'fleet';
import { RUNNER_HOST_NAMES } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { vi } from 'vitest';

import { GlanceBoard } from './glance-board';

const NOW = Date.now();
const completedAt = (ageMs: number) => new Date(NOW - ageMs).toISOString();

/**
 * `chunk-lanes.ts`'s lanes decide where in the list a chunk lands — this fixture
 * picks a chunk for every lane, plus terminal chunks outside the rolling window
 * that the list leaves out.
 */
const CHUNKS = [
  {
    chunk_id: 'ch_01needshuman0000000000000000',
    graph_id: 'gr_1',
    status: 'needs_human',
    current_node_id: 'nd_review',
    current_node_name: 'review',
    model: 'claude-opus-4-8',
    runner_id: 'r1',
  },
  {
    chunk_id: 'ch_01waitinghuman00000000000000',
    graph_id: 'gr_1',
    status: 'waiting_on_human',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'r2',
  },
  {
    chunk_id: 'ch_01running000000000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'r3',
    runner_name: 'r-claude',
    project_id: 'pj_alpha',
    cost: { cost_usd: 2.03, cost_partial: false, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 },
  },
  {
    chunk_id: 'ch_01readyfirst0000000000000000',
    graph_id: 'gr_1',
    status: 'ready',
    current_node_id: 'nd_test',
    current_node_name: 'test',
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01readysecond000000000000000',
    graph_id: 'gr_1',
    status: 'ready',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01donerecent0000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: completedAt(60 * 60 * 1000),
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
    work_refs: [{ label: 'blizzard#79' }],
  },
  {
    chunk_id: 'ch_01stoppedrecent00000000000000',
    graph_id: 'gr_1',
    status: 'stopped',
    terminal: true,
    completed_at: completedAt(2 * 60 * 60 * 1000),
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01doneold00000000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: completedAt(25 * 60 * 60 * 1000),
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01donemissing000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    terminal: true,
    completed_at: null,
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01notready00000000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
  {
    chunk_id: 'ch_01notreadylater000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
];

// The backlog's hub order deliberately differs from the chunks list's order.
const BACKLOG = [
  { chunk_id: 'ch_01notreadylater000000000000', graph_id: 'gr_1', position: 0, work_refs: [] },
  { chunk_id: 'ch_01notready00000000000000000', graph_id: 'gr_1', position: 1, work_refs: [] },
];

const DECISIONS = [
  {
    decision_id: 'dc_1',
    chunk_id: 'ch_01needshuman0000000000000000',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-16T00:00:01Z',
    choices: [{ name: 'approve', description: '' }],
  },
];

const PROJECTS = { projects: [{ project_id: 'pj_alpha', slug: 'alpha', name: 'Alpha', color: 'teal' }] };

// The API's dispatch order deliberately differs from the chunks list's order.
const QUEUE = [
  { chunk_id: 'ch_01readysecond000000000000000', graph_id: 'gr_1', position: 0 },
  { chunk_id: 'ch_01readyfirst0000000000000000', graph_id: 'gr_1', position: 1 },
];

// The open ask names the waiting_on_human fixture above, whose card carries its question.
const QUESTIONS = [
  {
    question_id: 'qn_01ask00000000000000000000000',
    chunk_id: 'ch_01waitinghuman00000000000000',
    runner_id: 'r2',
    question: '401 vs 403 for expired tokens?',
    options: [],
  },
];

const RUNNERS = [
  { runner_id: 'r1', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: true, hub_paused: false },
  { runner_id: 'r2', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: true, hub_paused: false },
  { runner_id: 'r3', workspace_id: 'ws', registered_at: '2026-07-20T00:00:00Z', last_seen_at: '2026-07-20T00:00:00Z', online: false, hub_paused: false },
];

const SPEND = {
  cost_usd: 18.4,
  cost_partial: false,
  input_tokens: 4_000_000,
  output_tokens: 2_000_000,
  cache_read_tokens: 100_000,
  cache_create_tokens: 0,
  since: '2026-07-20T00:00:00Z',
};

const ids = (el: HTMLElement): (string | null)[] =>
  [...el.querySelectorAll('[data-testid="glance-list"] > li')].map((li) => li.getAttribute('data-chunk') ?? li.getAttribute('data-testid'));

const card = (el: HTMLElement, chunkId: string): HTMLElement | null => el.querySelector(`[data-testid="glance-card"][data-chunk="${chunkId}"]`);

describe('GlanceBoard — one list and the vitals', () => {
  let stub: RequestClientStub;

  /** Answer the board's reads, `overrides` first. */
  function stubHub(overrides: (method: string, path: string) => unknown = () => undefined): void {
    stub = stubRequestClient(hubClient, (method, path) => {
      const override = overrides(method, path);
      if (override !== undefined) return override;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      if (method === 'GET' && path === '/api/queue') return { entries: QUEUE };
      if (method === 'GET' && path === '/api/backlog') return { entries: BACKLOG };
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/questions') return QUESTIONS;
      if (method === 'GET' && path === '/api/decisions') return { decisions: DECISIONS };
      if (method === 'GET' && path === '/api/projects') return PROJECTS;
      if (method === 'GET' && path === '/api/runners') return { runners: RUNNERS };
      if (method === 'GET' && path === '/api/health') return { status: 'ok' };
      if (method === 'GET' && path === '/api/spend') return SPEND;
      return {};
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

  it('lists needs, waiting, running, READY in queue order, the divider, BACKLOG in backlog order, then done today', async () => {
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
      'ch_01donerecent0000000000000000',
      'ch_01stoppedrecent00000000000000',
    ]);
  });

  it('keeps the backlog in the chunk read\'s order, unread, without queue:reorder', async () => {
    stub.restore();
    stubHub((method, path) => (method === 'GET' && path === '/api/me' ? { ...OPERATOR_ME_RESPONSE, permissions: [] } : undefined));
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);

    const list = ids(fixture.nativeElement as HTMLElement);
    expect(list.slice(list.indexOf('glance-ready-divider') + 1, -2)).toEqual(['ch_01notready00000000000000000', 'ch_01notreadylater000000000000']);
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

  it('meta-lines a running card with its node, its runner by host and name, and its spend, tinted by its project', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const running = card(fixture.nativeElement as HTMLElement, 'ch_01running000000000000000000');

    expect(running?.querySelector('.step')?.textContent?.trim()).toBe('build');
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
    stubHub((method, path) => (method === 'GET' && path === '/api/chunks' ? stubError(503, { detail: 'unavailable' }) : undefined));
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
    stubHub((method, path) =>
      method === 'GET' && path === '/api/runners' ? { runners: [...RUNNERS, { ...RUNNERS[2], runner_id: 'r4', retired: true }] } : undefined,
    );
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="vital-runners-up"]')?.textContent).toContain('2/3');
  });

  it('renders the fleet spend-today total via cost-format/formatTokens', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="glance-spend-row"]');
    expect(row?.textContent).toContain('$18.40');
    expect(row?.textContent).toContain('6.1M tok');
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
const ESTIMATE_CHUNKS = [
  {
    chunk_id: 'ch_01estimaterun0000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'r1',
    cost: {
      cost_usd: 0,
      cost_partial: false,
      estimated_cost_usd: 0.07,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
    },
  },
];

const ESTIMATE_SPEND = {
  cost_usd: 0,
  cost_partial: false,
  estimated_cost_usd: 0.07,
  input_tokens: 900,
  output_tokens: 400,
  cache_read_tokens: 0,
  cache_create_tokens: 0,
  since: '2026-07-20T00:00:00Z',
};

describe('GlanceBoard — cost estimate, no billed cost', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') return { chunks: ESTIMATE_CHUNKS, next_cursor: null };
      if (method === 'GET' && path === '/api/queue') return { entries: [] };
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/questions') return [];
      if (method === 'GET' && path === '/api/runners') return { runners: [] };
      if (method === 'GET' && path === '/api/health') return { status: 'ok' };
      if (method === 'GET' && path === '/api/spend') return ESTIMATE_SPEND;
      return {};
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

    expect(el.querySelector('[data-testid="glance-spend-value"]')?.textContent?.trim()).toBe('~$0.07');
  });
});

describe('GlanceBoard — work items', () => {
  let stub: RequestClientStub;
  const CHUNK_ID = 'ch_01workitems00000000000000000';

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') {
        return {
          chunks: [
            {
              chunk_id: CHUNK_ID,
              graph_id: 'gr_1',
              status: 'running',
              current_node_id: 'nd_build',
              current_node_name: 'build',
              runner_id: 'r1',
              work_refs: [{ source: 'wsrc_api', ref: '4', label: 'toy-api#4' }],
            },
          ],
          next_cursor: null,
        };
      }
      if (method === 'GET' && path === '/api/work-items') {
        return {
          chunks: { [CHUNK_ID]: [{ source: 'wsrc_api', ref: '4', label: 'toy-api#4', title: 'Add idempotency keys to POST /payments' }] },
        };
      }
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return { runners: [] };
      if (method === 'GET' && path === '/api/questions') return [];
      return {};
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
