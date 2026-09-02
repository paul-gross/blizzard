import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
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

/**
 * Exercises the `/gardening/runs-and-findings` container (blizzard#397 Phase 3) —
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
      if (method === 'GET' && path === '/api/runs') return [RUN_ROW, ESCALATED_RUN_ROW];
      if (method === 'GET' && path === '/api/runs/ch_1') return RUN_DELTA;
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

  it('shows the placeholder and no delta when the route carries no chunkId', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-findings-placeholder"]')).toBeTruthy();
    expect(el.querySelector('fleet-run-delta')).toBeNull();
  });

  it('mounts the run delta for the chunkId in the route param, keeping the list mounted too, with its chunk link and CLI verb', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-findings-placeholder"]')).toBeNull();
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
    expect(el.querySelector('[data-testid="gardening-runs-findings-placeholder"]')).toBeTruthy();
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
});
