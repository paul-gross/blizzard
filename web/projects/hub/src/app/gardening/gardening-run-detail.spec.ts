import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { BehaviorSubject } from 'rxjs';

import { GardeningRunDetail } from './gardening-run-detail';
import { GardeningRunsState } from './gardening-runs-state';

const RUN_ROW = {
  chunk_id: 'ch_1',
  routine_name: 'nightly',
  scope_slug: 'blizzard',
  mode: 'full',
  minted_at: '2026-01-10T00:00:00Z',
  outcome: 'done',
  escalation: null,
  delivered: [
    { finding_set_id: 'fins_1', revisions: { blizzard: 'abc123' }, measurement: '3 findings', added_count: 1, observed_count: 11, gone_count: 0 },
    { finding_set_id: 'fins_2', revisions: { blizzard: 'def456' }, measurement: null, added_count: 0, observed_count: 0, gone_count: 2 },
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
      observed: [{ finding_id: 'fnd_2', class: 'perf', locus: 'b.py:7', summary: 'still reproducing' }],
      gone: [{ finding_id: 'fnd_3', note: 'resolved' }],
    },
  ],
};

/**
 * Exercises the `/gardening/runs` detail child — the selected run's delta pane.
 * The list beside it is `gardening-runs-page.spec.ts`'s. `FleetRunDelta` owns its
 * own group rendering and is covered in its own spec; this spec proves the
 * container wires the right view model to it, including sourcing the run's minted
 * time from the shared run-list read rather than the delta read, which carries no
 * such instant.
 */
describe('GardeningRunDetail', () => {
  let stub: RequestClientStub;
  let paramMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  afterEach(() => stub?.restore());

  async function mount(chunkId: string | null, opts: { routeOverride?: (method: string, path: string) => unknown } = {}) {
    paramMap$ = new BehaviorSubject(convertToParamMap(chunkId === null ? {} : { chunkId }));
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/runs') return [RUN_ROW, ESCALATED_RUN_ROW];
      if (method === 'GET' && path === '/api/runs/ch_1') return RUN_DELTA;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GardeningRunDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        // A real `Router` (`provideRouter([])`), not a bare `{ navigate }` stub —
        // `FleetRunDelta`'s chunk `routerLink` needs one to resolve its own `href`.
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
        // Provided on `GardeningRunsPage` in the real route table; stood up directly
        // here, since this pane is what reads it.
        GardeningRunsState,
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDetail);
    await settle(fixture, 12);
    return { fixture };
  }

  it("shows fleet-run-delta's own empty state when nothing is selected", async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-run-delta"]')).toBeNull();
  });

  it('mounts the run delta for the chunkId in the route param, sourcing its minted time from the matching list row', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeNull();
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="gardening-run-delta-chunk-link"]');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
    expect(el.querySelector('[data-testid="gardening-run-delta-meta"] fleet-when')).toBeTruthy();
  });

  it('keeps a run delta’s added/observed/gone in their own distinct groups', async () => {
    const { fixture } = await mount('ch_1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="rd-group-added"]')?.textContent).toContain('unused import');
    expect(el.querySelector('[data-testid="rd-group-observed"]')?.textContent).toContain('F-2');
    expect(el.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('resolved');
  });

  it('re-selects the delta when the chunkId param changes without remounting the pane', async () => {
    const { fixture } = await mount('ch_1');
    const paneInstance = fixture.componentInstance;

    paramMap$.next(convertToParamMap({}));
    await settle(fixture);

    expect(fixture.componentInstance).toBe(paneInstance);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeTruthy();
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
