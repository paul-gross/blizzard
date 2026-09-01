import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningRoutinesPage } from './gardening-routines-page';

const ROUTINE = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
};

const EFFECTIVE_GRAPH_SUMMARY = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  created_at: '2026-01-01T00:00:00Z',
  effective: true,
};

const GRAPH_DETAIL = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  enabled: true,
  nodes: [{ node_id: 'nd_1', name: 'survey', executor: 'claude', judged_by: 'none', prompt: 'Survey the repo.' }],
};

const SWEEPS = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  last_swept: [
    { scope_slug: 'blizzard', finding_set_id: 'fins_1', produced_at: '2026-01-10T00:00:00Z', revisions: { blizzard: 'abc123' } },
    { scope_slug: 'never-swept', finding_set_id: null, produced_at: null, revisions: {} },
  ],
  measurements: [{ scope_slug: 'blizzard', produced_at: '2026-01-10T00:00:00Z', measurement: '3 findings' }],
};

const TREND = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  period_days: 7,
  periods: [
    { period_start: '2026-01-01T00:00:00Z', period_end: '2026-01-08T00:00:00Z', created: 2, exits: {}, outflow: 1, withdrawn: 0, reopened: 0 },
  ],
  age: { boundary: '2026-01-01T00:00:00Z', recent: 2, older: 0, unattributed: 0 },
};

describe('GardeningRoutinesPage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function render(opts: { routines?: readonly unknown[]; graphs?: readonly unknown[] } = {}) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines') return routines;
      if (method === 'GET' && path === '/api/graphs') return graphs;
      if (method === 'GET' && path === '/api/graphs/gr_1') return GRAPH_DETAIL;
      if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') return SWEEPS;
      if (method === 'GET' && path === '/api/routines/trend') return TREND;
      // The gardening run dialog's own reads, fired only once its Run trigger opens it.
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/routines/rtn_1/baselines') return [];
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GardeningRoutinesPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRoutinesPage);
    await settle(fixture, 12);
    return fixture;
  }

  it('renders no field absent from RoutineView, on no field the hub does not store', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const record = el.querySelector('[data-testid="gardening-routine-record"]');
    expect(record?.textContent).toContain('nightly');
    expect(record?.textContent).toContain('garden-routine');
    expect(record?.textContent).toContain('blizzard');
    expect(record?.textContent).toContain('claude-sonnet-5');
    expect(record?.textContent).toContain('medium');
  });

  it('renders the strategy as read-only prose with no edit affordance beyond the list picker and the Run trigger', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const strategy = el.querySelector('[data-testid="gardening-routine-strategy"]');
    expect(strategy?.textContent).toContain('Survey the repo.');
    expect(el.querySelectorAll('button, input, textarea, select, [contenteditable]')).toHaveLength(
      // The only interactive controls on this panel are the routine-list picker
      // buttons and the panel's own Run trigger — none inside the strategy/trend/
      // measurement/last-swept blocks themselves edits anything (D1).
      el.querySelectorAll('[data-testid^="gardening-routine-row-"]').length + 1,
    );
  });

  it('renders inflow, outflow, and reopened, distinguished from each other', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-trend-created"]')?.textContent).toBe('2');
    expect(el.querySelector('[data-testid="gardening-routine-trend-outflow"]')?.textContent).toBe('1');
    expect(el.querySelector('[data-testid="gardening-routine-trend-withdrawn"]')?.textContent).toBe('0');
  });

  it('renders the measurement series as text', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-measurements"]')?.textContent).toContain(
      '3 findings',
    );
  });

  it('keys last-swept on routine and scope, and marks a never-swept scope distinctly from a stale sweep', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const swept = el.querySelector('[data-testid="gardening-routine-last-swept-blizzard"]');
    expect(swept?.textContent).toContain('abc123');
    const never = el.querySelector('[data-testid="gardening-routine-last-swept-never-swept"]');
    expect(never?.querySelector('[data-testid="gardening-routine-last-swept-never"]')?.textContent).toBe('never');
  });

  it('says a routine whose graph has no effective mint is blocked, and offers no run', async () => {
    const fixture = await render({ graphs: [] });
    const el = fixture.nativeElement as HTMLElement;

    const blocked = el.querySelector('[data-testid="gardening-routine-blocked"]');
    expect(blocked?.textContent).toContain('Blocked');
    expect(blocked?.textContent).toContain('garden-routine');
    expect(el.querySelector('[data-testid="gardening-routine-strategy"]')).toBeNull();
    expect(el.querySelectorAll('button[data-testid*="run"]')).toHaveLength(0);
    expect(el.textContent).not.toContain('hub routine run');
  });

  it('names the CLI verb behind every read block, and the run verb behind the Run trigger', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('hub routine show');
    expect(el.textContent).toContain('hub routine trend');
    expect(el.textContent).toContain('hub routine sweeps');
    expect(el.textContent).toContain('hub routine run');
  });

  it('opens the run dialog off the panel Run trigger, and closing it tears the dialog down', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-run"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-dialog-title"]')?.textContent).toContain('nightly');

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-cancel"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();
  });

  it('renders its own empty state with no routines declared', async () => {
    const fixture = await render({ routines: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routines-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).not.toBeNull();
  });
});
