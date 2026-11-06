import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter, type ParamMap } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { FLEET_CLOCK, type GraphSummaryView, type MeResponse, type RoutineView } from 'fleet';
import {
  OPERATOR_ME_RESPONSE,
  gardenCadenceView,
  gardenChoreView,
  gardenGraphSummaryView,
  gardenMeasurementReadingView,
  gardenProposalCountsRowView,
  gardenProposalCountsView,
  gardenRoutineView,
  gardenScopeSweepView,
  gardenSweepsView,
  gardenTrendPeriodView,
  gardenTrendView,
  graphNodeView,
  graphView,
  runRowView,
  settle,
  stubError,
  stubHubClient,
  type RequestClientStub,
} from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';

import { GardeningRoutineDetail } from './gardening-routine-detail';

/** A read-only identity — every permission `OPERATOR_ME_RESPONSE` carries except
 * `graph:edit` — the default for tests unconcerned with the panel's gated
 * retire/enable controls (`gardening-scope-detail.spec.ts`'s own
 * `VIEWER_ME_RESPONSE`). */
const VIEWER_ME_RESPONSE: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'graph:edit'),
};

const ROUTINE = gardenRoutineView({
  routine_id: 'rtn_1',
  name: 'Nightly sweep',
  slug: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
});

const EFFECTIVE_GRAPH_SUMMARY = gardenGraphSummaryView();

const GRAPH_DETAIL = graphView({
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  enabled: true,
  nodes: [graphNodeView({ node_id: 'nd_1', name: 'survey', prompt: 'Survey the repo.' })],
  edges: [],
});

const SWEEPS = gardenSweepsView({
  routine_name: 'nightly',
  last_swept: [
    gardenScopeSweepView({ finding_set_id: 'fins_1', produced_at: '2026-01-10T00:00:00Z', revisions: { blizzard: 'abc123' } }),
    gardenScopeSweepView({ scope_slug: 'never-swept' }),
  ],
  measurements: [gardenMeasurementReadingView({ measurement: '3 findings' })],
});

const TREND = gardenTrendView({
  routine_name: 'nightly',
  periods: [gardenTrendPeriodView({ created: 2, outflow: 1 })],
  age: { boundary: '2026-01-01T00:00:00Z', recent: 2, older: 0, unattributed: 0 },
});

const PROPOSAL_COUNTS = gardenProposalCountsView({
  routine: 'nightly',
  rows: [gardenProposalCountsRowView({ open: 2, passed: 1, accepted_with_item: 3, created: 6 })],
});

type HubRoutes = Parameters<typeof stubHubClient>[0];

/**
 * Exercises the `/gardening/routines` detail child — the selected routine's
 * record, its related scopes, its read-only strategy, its three
 * health readings, and the Run trigger that opens the run dialog. The list beside
 * it, its blocked marking, and the selection's own route wiring are
 * `gardening-routines-page.spec.ts`'s.
 */
describe('GardeningRoutineDetail', () => {
  let stub: RequestClientStub;
  let paramMap$: BehaviorSubject<ParamMap>;

  afterEach(() => stub?.restore());

  async function render(
    opts: {
      routines?: RoutineView[];
      graphs?: GraphSummaryView[];
      me?: MeResponse;
      routes?: HubRoutes;
      params?: Record<string, string>;
    } = {},
  ) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    const me = opts.me ?? VIEWER_ME_RESPONSE;
    stub = stubHubClient({
      'GET /api/routines': () => routines,
      'GET /api/graphs': () => graphs,
      'GET /api/graphs/{graph_id}': () => GRAPH_DETAIL,
      'GET /api/routines/{routine_id}/sweeps': () => SWEEPS,
      'GET /api/routines/{routine_id}/scopes': () => ['blizzard'],
      'GET /api/routines/trend': () => TREND,
      'GET /api/routines/proposal-counts': () => PROPOSAL_COUNTS,
      // The gardening run dialog's own baselines read, fired only once its Run trigger opens it.
      'GET /api/routines/{routine_id}/baselines': () => [],
      // The run dialog's own scope picker (`gardening-run-dialog.ts`) injects
      // `injectHubScopesQuery` independently of this pane.
      'GET /api/scopes': () => [],
      'GET /api/runs': () => [],
      'GET /api/cadences': () => [],
      'GET /api/me': () => me,
      'POST /api/routines/{routine_id}/retire': (_request, { routine_id }) =>
        gardenRoutineView({ ...ROUTINE, routine_id, retired: true }),
      'POST /api/routines/{routine_id}/enable': (_request, { routine_id }) => gardenRoutineView({ ...ROUTINE, routine_id }),
      'PATCH /api/routines/{routine_id}': () => gardenRoutineView({ ...ROUTINE, graph_name: 'garden-delta' }),
      ...opts.routes,
    });
    paramMap$ = new BehaviorSubject(convertToParamMap(opts.params ?? {}));
    await TestBed.configureTestingModule({
      imports: [GardeningRoutineDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRoutineDetail);
    await settle(fixture, 12);
    return fixture;
  }

  it('cuts the trend read’s window from FLEET_CLOCK, 28 days back', async () => {
    const nowMs = Date.UTC(2026, 6, 16, 12, 0, 0);
    TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => nowMs });
    await render({ params: { routineSlug: 'nightly' } });

    const search = stub.forRoute('/api/routines/trend', 'GET').map((r) => r.search).join('\n');
    expect(search).toContain(`since=${encodeURIComponent(new Date(nowMs - 28 * 24 * 60 * 60 * 1000).toISOString())}`);
    expect(search).toContain(`until=${encodeURIComponent(new Date(nowMs).toISOString())}`);
  });

  it('resolves the routine by its slug, reads its trend and proposal counts by slug, and shows its display name', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-record"] h3')?.textContent).toBe('Nightly sweep');
    expect(stub.forRoute('/api/routines/trend', 'GET').map((r) => r.search).join('\n')).toContain('routine=nightly');
    expect(stub.forRoute('/api/routines/proposal-counts', 'GET').map((r) => r.search).join('\n')).toContain(
      'routine=nightly',
    );
  });

  it('never resolves a routine by its display name', async () => {
    const fixture = await render({ params: { routineSlug: 'Nightly sweep' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-record"]')).toBeNull();
  });

  it("lists the routine's recent runs, matched by the slug each run carries", async () => {
    const run = runRowView({
      chunk_id: 'ch_1',
      routine_name: 'nightly',
      scope_slug: 'blizzard',
      mode: 'full',
      minted_at: '2026-01-10T00:00:00Z',
      outcome: 'done',
      escalation: null,
      delivered: [],
    });
    const fixture = await render({
      params: { routineSlug: 'nightly' },
      routes: { 'GET /api/runs': () => [run, { ...run, chunk_id: 'ch_2', routine_name: 'Nightly sweep' }] },
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-fact-runs"]')?.textContent).toContain('1 in the');
  });

  it('shows its own empty state on the bare child route, selecting nothing', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-record"]')).toBeNull();
  });

  it('a routineSlug param naming an unknown routine resolves to the empty state, not a stale panel', async () => {
    const fixture = await render({ params: { routineSlug: 'ghost' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-record"]')).toBeNull();
  });

  it('renders the proposal counts panel alongside Activity and Strategy once a routine is selected', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-routine-proposal-counts-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(panel?.textContent).toContain('stale-docstring');
  });

  it('renders no proposal counts panel while nothing is selected', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-proposal-counts-panel"]')).toBeNull();
  });

  it('renders no field absent from RoutineView, on no field the hub does not store', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const record = el.querySelector('[data-testid="gardening-routine-panel"]');
    expect(record?.textContent).toContain('Nightly sweep');
    expect(record?.textContent).toContain('garden-routine');
    expect(record?.textContent).toContain('blizzard');
    expect(record?.textContent).toContain('claude-sonnet-5');
    expect(record?.textContent).toContain('medium');
  });

  it('names the cadence chore keeping the routine off the cadences read', async () => {
    const fixture = await render({
      params: { routineSlug: 'nightly' },
      routes: {
        'GET /api/cadences': () => [
          gardenCadenceView({ slug: 'evening', name: 'Evening', chores: [gardenChoreView({ routine: ROUTINE.slug })] }),
        ],
      },
    });
    const el = fixture.nativeElement as HTMLElement;

    const fact = el.querySelector('[data-testid="gardening-routine-fact-kept-by"]');
    expect(fact?.textContent).toContain('Evening');
    expect(fact?.textContent).toContain('chore nightly');
  });

  it('says no cadence keeps a routine no chore names', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-kept-by-none"]')?.textContent).toContain(
      'No cadence keeps it — it runs when started.',
    );
  });

  it('lists the related scopes, marking the routine default', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-routine-scopes"]');
    expect(section?.textContent).toContain('blizzard');
    expect(section?.querySelector('[data-testid="gardening-routine-scope-default"]')).toBeTruthy();
  });

  it('renders the strategy as read-only prose with no edit affordance beyond the Run trigger', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const strategy = el.querySelector('[data-testid="gardening-routine-strategy"]');
    expect(strategy?.textContent).toContain('Survey the repo.');
    // The Run trigger is this pane's only interactive control — nothing inside the
    // strategy/trend/measurement/last-swept blocks themselves edits anything.
    expect(el.querySelectorAll('button, input, textarea, select, [contenteditable]')).toHaveLength(1);
  });

  it('renders inflow, outflow, and reopened, distinguished from each other', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const facts = el.querySelector('[data-testid="gardening-routine-trend-facts"]');
    const dds = Array.from(facts?.querySelectorAll('dd') ?? []).map((d) => d.textContent);
    expect(dds).toEqual(['2', '1', '0', '0']);
  });

  it('renders the measurement series as text', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-measurements"]')?.textContent).toContain(
      '3 findings',
    );
  });

  it('keys last-swept on routine and scope, and marks a never-swept scope distinctly from a stale sweep', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const swept = el.querySelector('[data-testid="gardening-routine-last-swept-blizzard"]');
    expect(swept?.textContent).toContain('abc123');
    const never = el.querySelector('[data-testid="gardening-routine-last-swept-never-swept"]');
    expect(never?.querySelector('[data-testid="gardening-routine-last-swept-never"]')?.textContent).toBe('never');
  });

  it('says a routine whose graph has no effective mint is blocked, and offers no run', async () => {
    const fixture = await render({ graphs: [], params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const blocked = el.querySelector('[data-testid="gardening-routine-blocked"]');
    expect(blocked?.textContent).toContain('Blocked');
    expect(blocked?.textContent).toContain('garden-routine');
    // The strategy is the routine's own definition, so it stays readable while blocked —
    // only the Run action is withheld.
    expect(el.querySelector('[data-testid="gardening-routine-strategy"]')).not.toBeNull();
    expect(el.querySelectorAll('button[data-testid*="run"]')).toHaveLength(0);
  });

  it('names no CLI verb anywhere — the Run trigger is the whole affordance', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub routine run');
    expect(el.textContent).not.toContain('hub routine get');
    expect(el.textContent).not.toContain('hub routine trend');
    expect(el.textContent).not.toContain('hub routine sweeps');
  });

  it('opens the run dialog off the panel Run trigger, and closing it tears the dialog down', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-run"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-dialog-title"]')?.textContent).toContain('Nightly sweep');

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-cancel"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();
  });

  // --- Lifecycle: retire/enable, `GardeningScopeDetail`'s own shape --

  it('shows no lifecycle control for a read-only identity', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
  });

  it('shows the lifecycle control for an identity with graph:edit', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
  });

  it('retires a routine through POST /api/routines/{id}/retire once confirmed', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/routines/rtn_1/retire', 'POST')).toHaveLength(1);
  });

  it('hides Run once a retired routine reads back, and offers Re-enable instead of Retire', async () => {
    const fixture = await render({
      routines: [{ ...ROUTINE, retired: true }],
      me: OPERATOR_ME_RESPONSE,
      params: { routineSlug: 'nightly' },
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-run"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-retired-notice"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
  });

  it('leaves another routine’s Retire enabled while one routine’s Retire is pending, and re-disables on re-select', async () => {
    const OTHER_ROUTINE = { ...ROUTINE, routine_id: 'rtn_2', slug: 'weekly', name: 'weekly' };
    const fixture = await render({
      me: OPERATOR_ME_RESPONSE,
      routines: [ROUTINE, OTHER_ROUTINE],
      params: { routineSlug: 'nightly' },
      routes: {
        'GET /api/routines/{routine_id}/sweeps': (_request, { routine_id }) =>
          routine_id === 'rtn_2' ? { ...SWEEPS, routine_name: 'weekly' } : SWEEPS,
        'GET /api/routines/{routine_id}/scopes': (_request, { routine_id }) => (routine_id === 'rtn_2' ? [] : ['blizzard']),
      },
    });
    vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries').mockReturnValue(new Promise<void>(() => undefined));
    const el = fixture.nativeElement as HTMLElement;
    const retire = () => el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]');
    const tick = async (): Promise<void> => {
      for (let i = 0; i < 4; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        fixture.detectChanges();
      }
    };

    retire()?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await tick();
    expect(retire()?.disabled).toBe(true);

    paramMap$.next(convertToParamMap({ routineSlug: 'weekly' }));
    await tick();
    expect(retire()?.disabled).toBe(false);

    paramMap$.next(convertToParamMap({ routineSlug: 'nightly' }));
    await tick();
    expect(retire()?.disabled).toBe(true);
  });

  it('renders the retired override while Retire is pending, reverting to the real state on rejection', async () => {
    const fixture = await render({
      me: OPERATOR_ME_RESPONSE,
      params: { routineSlug: 'nightly' },
      routes: { 'POST /api/routines/{routine_id}/retire': () => stubError(404, { detail: 'unknown routine rtn_1' }) },
    });
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="gardening-routine-panel-state"]')?.textContent?.trim()).toBe('retired');
    expect(el.querySelector('[data-testid="gardening-routine-run"]')).toBeNull();
    // The control choice stays keyed off the real, unoverridden `retired` — the
    // routine is still enabled until the mutation settles, so Retire (not
    // Re-enable) is what the next click must fire.
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeNull();

    resolveInvalidate();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-routine-panel-state"]')?.textContent?.trim()).toBe('enabled');
    expect(el.querySelector('[data-testid="gardening-routine-panel-error"]')?.textContent).toContain(
      'unknown routine rtn_1',
    );
  });

  it('submits an edited graph through PATCH /api/routines/{id}', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-edit"]')!.click();
    await fixture.whenStable();
    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-routine-panel-graph-input"]')!;
    input.value = 'garden-delta';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-submit"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/routines/rtn_1', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ graph_name: 'garden-delta' });
  });

  it('withholds Edit from an identity without graph:edit', async () => {
    const fixture = await render({ params: { routineSlug: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-edit"]')).toBeNull();
  });
});
