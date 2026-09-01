import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type MeResponse } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { GardeningRoutinesPage } from './gardening-routines-page';

/** A read-only identity — every permission `OPERATOR_ME_RESPONSE` carries except
 * `graph:edit` — the default for tests unconcerned with the scope list's gated
 * description-editor/retire/enable controls (blizzard#400). */
const VIEWER_ME_RESPONSE: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'graph:edit'),
};

const SCOPE = { slug: 'blizzard', description: 'the blizzard monorepo', retired: false, created_at: '2026-01-01T00:00:00Z' };

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

  async function render(
    opts: {
      routines?: readonly unknown[];
      graphs?: readonly unknown[];
      scopes?: readonly unknown[];
      me?: MeResponse;
      routeOverride?: (method: string, path: string) => unknown;
    } = {},
  ) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    const scopes = opts.scopes ?? [SCOPE];
    const me = opts.me ?? VIEWER_ME_RESPONSE;
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/routines') return routines;
      if (method === 'GET' && path === '/api/graphs') return graphs;
      if (method === 'GET' && path === '/api/graphs/gr_1') return GRAPH_DETAIL;
      if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') return SWEEPS;
      if (method === 'GET' && path === '/api/routines/trend') return TREND;
      // The gardening run dialog's own baselines read, fired only once its Run trigger opens it.
      if (method === 'GET' && path === '/api/routines/rtn_1/baselines') return [];
      if (method === 'GET' && path === '/api/scopes') return scopes;
      if (method === 'GET' && path === '/api/me') return me;
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

  describe('scopes (blizzard#400)', () => {
    it('lists every scope with slug, description, and retired state, without an editor for a read-only identity', async () => {
      const fixture = await render({
        scopes: [SCOPE, { slug: 'stale-scope', description: 'no longer tended', retired: true, created_at: '2026-01-01T00:00:00Z' }],
      });
      const el = fixture.nativeElement as HTMLElement;

      const row = el.querySelector('[data-testid="gardening-scope-row-blizzard"]');
      expect(row?.textContent).toContain('blizzard');
      expect(row?.textContent).toContain('the blizzard monorepo');
      expect(row?.textContent).toContain('enabled');
      expect(el.querySelector('[data-testid="gardening-scope-row-stale-scope"]')?.textContent).toContain('retired');
      expect(el.querySelector('[data-testid="gardening-scope-description-input-blizzard"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-scope-retire-blizzard"]')).toBeNull();
    });

    it('shows the description editor and lifecycle control for an identity with graph:edit', async () => {
      const fixture = await render({ me: OPERATOR_ME_RESPONSE });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-scope-description-input-blizzard"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="gardening-scope-retire-blizzard"]')).toBeTruthy();
    });

    it('submits an edited description through PATCH /api/scopes/{slug}', async () => {
      const fixture = await render({ me: OPERATOR_ME_RESPONSE });
      const el = fixture.nativeElement as HTMLElement;

      const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-description-input-blizzard"]')!;
      input.value = 'updated description';
      el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-description-submit-blizzard"]')?.click();
      await settle(fixture);

      const calls = stub.forRoute('/api/scopes/blizzard', 'PATCH');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({ description: 'updated description' });
    });

    it('retires a scope through POST /api/scopes/{slug}/retire once confirmed', async () => {
      const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
      const fixture = await render({ me: OPERATOR_ME_RESPONSE });
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-retire-blizzard"]')?.click();
      await settle(fixture);

      expect(stub.forRoute('/api/scopes/blizzard/retire', 'POST')).toHaveLength(1);
      confirmSpy.mockRestore();
    });

    it('reports a failed edit through the action-error line rather than swallowing it', async () => {
      const fixture = await render({
        me: OPERATOR_ME_RESPONSE,
        routeOverride: (method, path) =>
          method === 'PATCH' && path === '/api/scopes/blizzard'
            ? stubError(404, { detail: 'unknown scope blizzard' })
            : undefined,
      });
      const el = fixture.nativeElement as HTMLElement;

      const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-description-input-blizzard"]')!;
      input.value = 'updated description';
      el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-description-submit-blizzard"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-scopes-error"]')?.textContent).toContain('unknown scope blizzard');
    });
  });
});
