import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';

import { GardeningRunsFindingsPage } from './gardening-runs-findings-page';

const RUN_ROW = {
  chunk_id: 'ch_1',
  routine_name: 'nightly',
  scope_slug: 'blizzard',
  mode: 'full',
  minted_at: '2026-01-10T00:00:00Z',
  outcome: 'done',
  escalation: null,
  delivered: [
    { finding_set_id: 'fins_1', revisions: { blizzard: 'abc123' }, measurement: '3 findings' },
    { finding_set_id: 'fins_2', revisions: { blizzard: 'def456' }, measurement: null },
  ],
};

const ESCALATED_RUN_ROW = {
  chunk_id: 'ch_2',
  routine_name: 'nightly',
  scope_slug: 'web',
  mode: 'delta',
  minted_at: '2026-01-11T00:00:00Z',
  outcome: 'needs_human',
  escalation: {
    node_name: 'survey',
    takeover_command: 'blizzard hub chunk takeover ch_2',
    wrapped_takeover_command: '',
  },
  delivered: [],
};

const RUN_DELTA = {
  chunk_id: 'ch_1',
  routine_name: 'nightly',
  scope_slug: 'blizzard',
  mode: 'full',
  outcome: 'done',
  escalation: null,
  sets: [
    {
      finding_set_id: 'fins_1',
      revisions: { blizzard: 'abc123' },
      measurement: '3 findings',
      added: [{ finding_id: 'fnd_1', class: 'style', locus: 'a.py:1', summary: 'unused import', introduced: null }],
      observed: ['fnd_2'],
      gone: [{ finding_id: 'fnd_3', note: 'resolved' }],
    },
  ],
};

const ROUTINES = [
  { routine_id: 'rt_1', name: 'nightly', graph_name: 'sweep', default_scope_slug: 'blizzard', created_at: '2026-01-01T00:00:00Z' },
];

const SCOPES = [{ slug: 'blizzard', description: 'the blizzard repo', created_at: '2026-01-01T00:00:00Z' }];

function findingFixture(overrides: { state: string } & Record<string, unknown>) {
  return {
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    observed_count: 1,
    last_seen_at: '2026-01-05T00:00:00Z',
    introduced: '2026-01-01T00:00:00Z',
    note: null,
    live: overrides.state === 'live',
    ...overrides,
  };
}

const FINDING_LIVE = findingFixture({
  finding_id: 'fnd_10',
  class: 'stale-docstring',
  locus: 'a.py:1',
  summary: 'summary a',
  state: 'live',
});
const FINDING_GONE = findingFixture({
  finding_id: 'fnd_11',
  class: 'unused-import',
  locus: 'b.py:2',
  summary: 'summary b',
  state: 'gone',
  note: 'not seen in the last sweep',
});
const FINDING_RESOLVED_1 = findingFixture({
  finding_id: 'fnd_12',
  class: 'stale-docstring',
  locus: 'c.py:3',
  summary: 'summary c',
  state: 'resolved',
  note: 'fixed',
});
const FINDING_RESOLVED_2 = findingFixture({
  finding_id: 'fnd_13',
  class: 'stale-docstring',
  locus: 'd.py:4',
  summary: 'summary d',
  state: 'resolved',
});
const FINDING_GONE_CONFIRMED = findingFixture({
  finding_id: 'fnd_14',
  class: 'unused-import',
  locus: 'e.py:5',
  summary: 'summary e',
  state: 'gone-confirmed',
  note: 'confirmed gone',
});
const FINDING_WONT_FIX = findingFixture({
  finding_id: 'fnd_15',
  class: 'stale-docstring',
  locus: 'f.py:6',
  summary: 'summary f',
  state: 'wont-fix',
  note: 'not worth it',
});

const BUCKET = [
  FINDING_LIVE,
  FINDING_GONE,
  FINDING_RESOLVED_1,
  FINDING_RESOLVED_2,
  FINDING_GONE_CONFIRMED,
  FINDING_WONT_FIX,
];

const PROPOSAL_ACCEPTED_MINTED = {
  proposal_id: 'gp_1',
  routine_name: 'nightly',
  class: 'stale-docstring',
  title: 'Extract the shared helper',
  body: 'Three call sites duplicate this logic.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fnd_10'],
  closure: {
    closure: 'accepted',
    reason: null,
    closed_by: 'u_1',
    closed_at: '2026-01-02T00:00:00Z',
    item_outcome: 'minted',
    source: 'hub',
    ref: '42',
  },
};

const WORK_ITEM_42 = {
  source: 'hub',
  ref: '42',
  label: 'hub#42',
  web_url: '/board/chunk/ch_9',
  title: 't',
  body: 'b',
  author: { kind: 'user' },
  closure: null,
  closed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  edited_at: '2026-01-01T00:00:00Z',
  stated_priority: null,
};

/**
 * Exercises the `/gardening/runs-and-findings` container (blizzard#401 Phase 3) —
 * the run list is always mounted, and the optional `chunkId` route param drives
 * whether the delta shows, `graphs-page.spec.ts`'s own stubbed-`ActivatedRoute`
 * shape. `FleetRunList`/`FleetRunDelta` own their own row/group rendering and are
 * covered in their own specs; this spec proves the container wires the right rows
 * and view model to them, resolves `KitAsyncStateValue` correctly, turns a pick into
 * a navigation, and names both CLI verbs (`hub run list`, `hub run show`).
 */
describe('GardeningRunsFindingsPage', () => {
  let stub: RequestClientStub;
  let paramMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  afterEach(() => stub?.restore());

  async function mount(
    chunkId: string | null,
    opts: { routeOverride?: (method: string, path: string) => unknown } = {},
  ) {
    paramMap$ = new BehaviorSubject(convertToParamMap(chunkId === null ? {} : { chunkId }));
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runs') return [RUN_ROW, ESCALATED_RUN_ROW];
      if (method === 'GET' && path === '/api/runs/ch_1') return RUN_DELTA;
      if (method === 'GET' && path === '/api/findings') return [];
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return ROUTINES;
      if (method === 'GET' && path === '/api/scopes') return SCOPES;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GardeningRunsFindingsPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
      ],
    }).compileComponents();
    // A real `Router` (`provideRouter([])`), not a bare `{ navigate }` stub
    // (`graphs-page.spec.ts`'s own shape) — `FleetRunDelta`'s chunk `routerLink`
    // needs a real `Router` to resolve its own `href`, which a stub object lacks.
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(GardeningRunsFindingsPage);
    await settle(fixture, 12);
    return { fixture, navigate };
  }

  it('renders its own empty state with no runs recorded', async () => {
    const { fixture } = await mount(null, { routeOverride: (method, path) => (method === 'GET' && path === '/api/runs' ? [] : undefined) });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-findings-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
  });

  it('lists every run with its routine, scope, mode, and outcome, keeping each delivered finding set its own distinct entry', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    expect(row?.textContent).toContain('nightly/blizzard');
    expect(row?.textContent).toContain('mode=full');
    expect(row?.textContent).toContain('done');
    expect(el.querySelector('[data-testid="gardening-run-set-fins_1"]')?.textContent).toContain('abc123');
    expect(el.querySelector('[data-testid="gardening-run-set-fins_2"]')?.textContent).toContain('def456');
  });

  it('renders an escalated row distinctly from a normal row', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    const normal = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    const escalated = el.querySelector('[data-testid="gardening-run-row-ch_2"]');
    expect(normal?.classList.contains('rl-row--escalated')).toBe(false);
    expect(escalated?.classList.contains('rl-row--escalated')).toBe(true);
    expect(escalated?.querySelector('[data-testid="rl-escalated-note"]')).toBeTruthy();
  });

  it('names the run list CLI verb', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('hub run list');
  });

  it('navigates to runs-and-findings/:chunkId when a run is picked', async () => {
    const { fixture, navigate } = await mount(null);

    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      '[data-testid="gardening-run-row-ch_1"]',
    )!.click();
    await settle(fixture);

    expect(navigate).toHaveBeenCalledWith(['/gardening', 'runs-and-findings', 'ch_1']);
  });

  it("shows fleet-run-delta's own empty state when the route carries no chunkId", async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-run-delta"]')).toBeNull();
  });

  it('mounts the run delta for the chunkId in the route param, keeping the list mounted too, with its chunk link and CLI verb', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeNull();
    expect(el.querySelector('fleet-run-list')).toBeTruthy();
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="gardening-run-delta-chunk-link"]');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
    expect(el.textContent).toContain('hub run show ch_1');
  });

  it('keeps a run delta’s added/observed/gone in their own distinct groups', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="rd-group-added"]')?.textContent).toContain('unused import');
    expect(el.querySelector('[data-testid="rd-group-observed"]')?.textContent).toContain('fnd_2');
    expect(el.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('resolved');
  });

  it('states the delta reads as what changed, not a current-state snapshot', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-caption"]')?.textContent).toContain(
      'not a current-state snapshot',
    );
  });

  it('re-selects the delta when the chunkId param changes without remounting the page', async () => {
    const { fixture } = await mount('ch_1');
    const pageInstance = fixture.componentInstance;

    paramMap$.next(convertToParamMap({}));
    await settle(fixture);

    expect(fixture.componentInstance).toBe(pageInstance);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeTruthy();
  });

  it('resolves a run-list read failure to the error state', async () => {
    const { fixture } = await mount(null, {
      routeOverride: (method, path) => (method === 'GET' && path === '/api/runs' ? stubError(500, {}) : undefined),
    });
    const el = fixture.nativeElement as HTMLElement;

    const list = fixture.debugElement.query(By.css('fleet-run-list'));
    expect(list.componentInstance.state()).toBe('error');
    expect(el.textContent).toContain('UNAVAILABLE');
  });

  it('resolves an unknown chunkId to the run delta’s error state', async () => {
    const { fixture } = await mount('ch_missing', {
      routeOverride: (method, path) =>
        method === 'GET' && path === '/api/runs/ch_missing' ? stubError(404, { detail: 'unknown run' }) : undefined,
    });

    const delta = fixture.debugElement.query(By.css('fleet-run-delta'));
    expect(delta.componentInstance.state()).toBe('error');
  });

  describe('the findings triage bucket (blizzard#402 Phase 4)', () => {
    it("renders the bucket's own rest state until a routine/scope is chosen", async () => {
      const { fixture } = await mount(null);
      const el = fixture.nativeElement as HTMLElement;

      const list = fixture.debugElement.query(By.css('fleet-finding-list'));
      expect(list.componentInstance.state()).toBe('empty');
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')).toBeNull();
    });

    it("reads the selected run's own routine and scope by default, tinting a gone row and dimming an exited row while keeping both rendered", async () => {
      const { fixture } = await mount('ch_1', {
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings' ? BUCKET : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      const live = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      const gone = el.querySelector('[data-testid="gardening-finding-row-fnd_11"]');
      const resolved = el.querySelector('[data-testid="gardening-finding-row-fnd_12"]');
      expect(live).toBeTruthy();
      expect(gone?.classList.contains('fl-row--gone')).toBe(true);
      expect(gone?.querySelector('[data-testid="fl-note"]')?.textContent).toContain('not seen in the last sweep');
      expect(resolved).toBeTruthy();
      expect(resolved?.classList.contains('fl-row--exited')).toBe(true);
    });

    it('narrows the rendered rows via the class and state filters', async () => {
      const { fixture } = await mount('ch_1', {
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings' ? BUCKET : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="gardening-finding-class-item-unused-import"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_11"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_14"]')).toBeTruthy();

      el.querySelector<HTMLElement>('[data-testid="gardening-finding-state-item-gone"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_11"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_14"]')).toBeNull();
    });

    it('separates outflow from withdrawn counts in the summary given a mixed bucket', async () => {
      const { fixture } = await mount('ch_1', {
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings' ? BUCKET : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      const summary = el.querySelector('[data-testid="gardening-findings-summary"]')?.textContent ?? '';
      expect(summary).toContain('3');
      expect(summary).toContain('1');
    });

    it('resolves an accepted-and-minted proposal work item beside a finding that is still live', async () => {
      const { fixture } = await mount('ch_1', {
        routeOverride: (method, path) => {
          if (method === 'GET' && path === '/api/findings') return BUCKET;
          if (method === 'GET' && path === '/api/garden-proposals') return [PROPOSAL_ACCEPTED_MINTED];
          if (method === 'GET' && path === '/api/work-sources/hub/items/42') return WORK_ITEM_42;
          return undefined;
        },
      });
      await settle(fixture, 8);
      const el = fixture.nativeElement as HTMLElement;

      const row = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      expect(row?.querySelector('[data-testid="fl-state"]')?.textContent).toContain('live');
      const link = row?.querySelector<HTMLAnchorElement>('[data-testid="fl-work-item-link"]');
      expect(link?.textContent).toBe('hub#42');
      expect(link?.getAttribute('href')).toBe('/board/chunk/ch_9');
    });
  });

  describe('the bulk-action triage dialog (blizzard#402 Phase 3)', () => {
    it('forwards chunk:control to the finding list, withholding it for a read-only identity', async () => {
      const { fixture } = await mount(null, {
        routeOverride: (method, path) => {
          if (method === 'GET' && path === '/api/me') {
            return { ...OPERATOR_ME_RESPONSE, permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'chunk:control') };
          }
          return undefined;
        },
      });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-findings-select-all"]')).toBeNull();
    });

    it('opens the triage dialog when the finding list emits bulkTriage, closing it again on (closed)', async () => {
      const { fixture } = await mount('ch_1', {
        routeOverride: (method, path) => (method === 'GET' && path === '/api/findings' ? BUCKET : undefined),
      });
      const el = fixture.nativeElement as HTMLElement;

      const list = fixture.debugElement.query(By.css('fleet-finding-list'));
      list.componentInstance.bulkTriage.emit({ verb: 'resolve', findingIds: ['fnd_10'] });
      await settle(fixture);

      const dialog = el.querySelector('[data-testid="gardening-finding-triage-dialog"]');
      expect(dialog).toBeTruthy();
      expect(dialog?.textContent).toContain('Resolve 1 finding');

      const dialogComponent = fixture.debugElement.query(By.css('app-gardening-finding-triage-dialog'));
      dialogComponent.componentInstance.closed.emit();
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-finding-triage-dialog"]')).toBeNull();
    });
  });
});
