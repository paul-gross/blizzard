import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GlanceBoard } from './glance-board';

/**
 * `chunk-lanes.ts`'s `STATUS_TONE` decides which section a chunk lands in — this
 * fixture picks one chunk per bucket, plus a `not_ready` chunk that must land in
 * neither (the ready rail's own concern on the desktop board, out of scope here).
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
    cost: { cost_usd: 2.03, cost_partial: false, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 },
  },
  {
    chunk_id: 'ch_01donetoday00000000000000000',
    graph_id: 'gr_1',
    status: 'done',
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
    pm_pointers: [{ label: 'blizzard#79' }],
  },
  {
    chunk_id: 'ch_01notready00000000000000000',
    graph_id: 'gr_1',
    status: 'not_ready',
    current_node_id: null,
    model: 'claude-opus-4-8',
    runner_id: null,
  },
];

// The open ask names the SAME chunk as the waiting_on_human fixture above — the
// "Needs you" bucket must dedupe to one row (the ask's own text), not two.
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

describe('GlanceBoard — attention bucketing and vitals', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') return CHUNKS;
      if (method === 'GET' && path === '/api/questions') return QUESTIONS;
      if (method === 'GET' && path === '/api/runners') return { runners: RUNNERS };
      if (method === 'GET' && path === '/api/health') return { status: 'ok' };
      if (method === 'GET' && path === '/api/spend') return SPEND;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GlanceBoard],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('folds an open ask and a needs_human chunk into "Needs you", deduping the chunk an ask already names', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="needs-you-row"]');
    // Two rows: the ask (waiting_on_human, folded with its question) and the
    // needs_human chunk — not three, even though one chunk backs an open ask.
    expect(rows).toHaveLength(2);
    expect(el.querySelector('[data-chunk="ch_01waitinghuman00000000000000"]')?.textContent).toContain(
      '401 vs 403 for expired tokens?',
    );
    expect(el.querySelector('[data-chunk="ch_01needshuman0000000000000000"]')).toBeTruthy();
    // Neither the running, done, nor not_ready fixture chunk shows here.
    expect(el.querySelector('[data-testid="needs-you-row"][data-chunk="ch_01running000000000000000000"]')).toBeNull();
  });

  it('lands a running chunk in "In motion" with its runner, node, and spend', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="in-motion-row"]');
    expect(rows).toHaveLength(1);
    const row = el.querySelector('[data-testid="in-motion-row"][data-chunk="ch_01running000000000000000000"]');
    expect(row).toBeTruthy();
    expect(row?.textContent).toContain('r3');
    expect(row?.textContent).toContain('build');
    expect(row?.querySelector('[data-testid="in-motion-cost"]')?.textContent).toContain('$2.03');
  });

  it('lands a completed chunk in "Done today" with its PM pointer', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="done-today-row"]');
    expect(rows).toHaveLength(1);
    expect(el.querySelector('[data-chunk="ch_01donetoday00000000000000000"]')?.textContent).toContain('blizzard#79');
  });

  it('never buckets a not_ready chunk into any of the three attention sections', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-chunk="ch_01notready00000000000000000"]')).toBeNull();
  });

  it('derives the vitals strip from the same buckets plus the runner registry', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="vital-needs-you"]')?.textContent).toContain('2');
    expect(el.querySelector('[data-testid="vital-running"]')?.textContent).toContain('1');
    // r1 and r2 online, r3 offline — "2/3 runners up".
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

  it('renders status pills in the soft variant (mock screen C\'s muted, fully-rounded pill)', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    for (const testid of ['needs-you-row', 'in-motion-row', 'done-today-row']) {
      const badge = el.querySelector(`[data-testid="${testid}"] .badge`);
      expect(badge?.classList.contains('soft')).toBe(true);
      expect(badge?.classList.contains('pill')).toBe(false);
    }
  });

  it('colors each section header per the mock — red/cyan/green — with the count alongside it', async () => {
    const fixture = TestBed.createComponent(GlanceBoard);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const label = (panel: string) => el.querySelector(`[data-testid="${panel}"] .lbl`) as HTMLElement;
    expect(label('needs-you-panel').style.color).toBe('var(--red)');
    expect(label('in-motion-panel').style.color).toBe('var(--cyan)');
    expect(label('done-today-panel').style.color).toBe('var(--green)');

    expect(el.querySelector('[data-testid="needs-you-count"]')?.textContent).toContain('2');
    expect(el.querySelector('[data-testid="in-motion-count"]')?.textContent).toContain('1');
    expect(el.querySelector('[data-testid="done-today-count"]')?.textContent).toContain('1');
  });
});
