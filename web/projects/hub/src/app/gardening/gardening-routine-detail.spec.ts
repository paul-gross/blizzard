import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type MeResponse } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubRequestClient, type RequestClientStub } from 'fleet/testing';
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

const PROPOSAL_COUNTS = {
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  routine: 'nightly',
  rows: [
    {
      routine_name: 'nightly',
      class: 'stale-docstring',
      open: 2,
      passed: 1,
      accepted_with_item: 3,
      accepted_without_item: 0,
      created: 6,
    },
  ],
};

/**
 * Exercises the `/gardening/routines` detail child — the selected routine's
 * record, its related scopes, its read-only strategy, its three
 * health readings, and the Run trigger that opens the run dialog. The list beside
 * it, its blocked marking, and the selection's own route wiring are
 * `gardening-routines-page.spec.ts`'s.
 */
describe('GardeningRoutineDetail', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(
    opts: {
      routines?: readonly unknown[];
      graphs?: readonly unknown[];
      me?: MeResponse;
      routeOverride?: (method: string, path: string) => unknown;
      params?: Record<string, string>;
    } = {},
  ) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    const me = opts.me ?? VIEWER_ME_RESPONSE;
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/routines') return routines;
      if (method === 'GET' && path === '/api/graphs') return graphs;
      if (method === 'GET' && path === '/api/graphs/gr_1') return GRAPH_DETAIL;
      if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') return SWEEPS;
      if (method === 'GET' && path === '/api/routines/rtn_1/scopes') return ['blizzard'];
      if (method === 'GET' && path === '/api/routines/trend') return TREND;
      if (method === 'GET' && path === '/api/routines/proposal-counts') return PROPOSAL_COUNTS;
      // The gardening run dialog's own baselines read, fired only once its Run trigger opens it.
      if (method === 'GET' && path === '/api/routines/rtn_1/baselines') return [];
      // The run dialog's own scope picker (`gardening-run-dialog.ts`) injects
      // `injectHubScopesQuery` independently of this pane.
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return me;
      return {};
    });
    const paramMap$ = new BehaviorSubject(convertToParamMap(opts.params ?? {}));
    await TestBed.configureTestingModule({
      imports: [GardeningRoutineDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRoutineDetail);
    await settle(fixture, 12);
    return fixture;
  }

  it('shows its own empty state on the bare child route, selecting nothing', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-record"]')).toBeNull();
  });

  it('a routineName param naming an unknown routine resolves to the empty state, not a stale panel', async () => {
    const fixture = await render({ params: { routineName: 'ghost' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-record"]')).toBeNull();
  });

  it('renders the proposal counts panel alongside Activity and Strategy once a routine is selected', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
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
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const record = el.querySelector('[data-testid="gardening-routine-record"]');
    expect(record?.textContent).toContain('nightly');
    expect(record?.textContent).toContain('garden-routine');
    expect(record?.textContent).toContain('blizzard');
    expect(record?.textContent).toContain('claude-sonnet-5');
    expect(record?.textContent).toContain('medium');
  });

  it('lists the related scopes, marking the routine default', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-routine-scopes"]');
    expect(section?.textContent).toContain('blizzard');
    expect(section?.querySelector('[data-testid="gardening-routine-scope-default"]')).toBeTruthy();
  });

  it('renders the strategy as read-only prose with no edit affordance beyond the Run trigger', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const strategy = el.querySelector('[data-testid="gardening-routine-strategy"]');
    expect(strategy?.textContent).toContain('Survey the repo.');
    // The Run trigger is this pane's only interactive control — nothing inside the
    // strategy/trend/measurement/last-swept blocks themselves edits anything (D1).
    expect(el.querySelectorAll('button, input, textarea, select, [contenteditable]')).toHaveLength(1);
  });

  it('renders inflow, outflow, and reopened, distinguished from each other', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const facts = el.querySelector('[data-testid="gardening-routine-trend-facts"]');
    const dds = Array.from(facts?.querySelectorAll('dd') ?? []).map((d) => d.textContent);
    expect(dds).toEqual(['2', '1', '0', '0']);
  });

  it('renders the measurement series as text', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-measurements"]')?.textContent).toContain(
      '3 findings',
    );
  });

  it('keys last-swept on routine and scope, and marks a never-swept scope distinctly from a stale sweep', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    const swept = el.querySelector('[data-testid="gardening-routine-last-swept-blizzard"]');
    expect(swept?.textContent).toContain('abc123');
    const never = el.querySelector('[data-testid="gardening-routine-last-swept-never-swept"]');
    expect(never?.querySelector('[data-testid="gardening-routine-last-swept-never"]')?.textContent).toBe('never');
  });

  it('says a routine whose graph has no effective mint is blocked, and offers no run', async () => {
    const fixture = await render({ graphs: [], params: { routineName: 'nightly' } });
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
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub routine run');
    expect(el.textContent).not.toContain('hub routine show');
    expect(el.textContent).not.toContain('hub routine trend');
    expect(el.textContent).not.toContain('hub routine sweeps');
  });

  it('opens the run dialog off the panel Run trigger, and closing it tears the dialog down', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-run"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="run-dialog-title"]')?.textContent).toContain('nightly');

    el.querySelector<HTMLButtonElement>('[data-testid="run-dialog-cancel"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-run-dialog"]')).toBeNull();
  });

  // --- Lifecycle: retire/enable, `GardeningScopeDetail`'s own shape --

  it('shows no lifecycle control for a read-only identity', async () => {
    const fixture = await render({ params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
  });

  it('shows the lifecycle control for an identity with graph:edit', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { routineName: 'nightly' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
  });

  it('retires a routine through POST /api/routines/{id}/retire once confirmed', async () => {
    const fixture = await render({ me: OPERATOR_ME_RESPONSE, params: { routineName: 'nightly' } });
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
      params: { routineName: 'nightly' },
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-run"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-retired-notice"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
  });

  it('renders the retired override while Retire is pending, reverting to the real state on rejection', async () => {
    const fixture = await render({
      me: OPERATOR_ME_RESPONSE,
      params: { routineName: 'nightly' },
      routeOverride: (method, path) =>
        method === 'POST' && path === '/api/routines/rtn_1/retire'
          ? stubError(404, { detail: 'unknown routine rtn_1' })
          : undefined,
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
});
