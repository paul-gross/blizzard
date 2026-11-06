import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type GraphSummaryView, type RoutineView, ViewportService } from 'fleet';
import {
  gardenGraphSummaryView,
  gardenRoutineView,
  settle,
  stubHubClient,
  type RequestClientStub,
} from 'fleet/testing';

import { GardeningRoutinesPage } from './gardening-routines-page';

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

/** Stands in for `GardeningRoutineDetail`, whose own behavior is
 * `gardening-routine-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-routine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="routine-detail-stub"></span>',
})
class TestRoutineDetail {}

@Component({
  selector: 'app-test-routines-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestRoutinesHost {}

/** The real route table's own shape for this tab (`app.routes.ts`), driven by the
 * real router — what is under test *is* that the parent reads its child's param
 * and survives the pick. */
const routes: Routes = [
  {
    path: 'gardening/routines',
    component: GardeningRoutinesPage,
    children: [
      { path: '', component: TestRoutineDetail },
      { path: ':routineSlug', component: TestRoutineDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/routines` list container — the routine list, its
 * per-row blocked marking, its selection highlight, and the navigation a row pick
 * performs. The detail pane's own content is `gardening-routine-detail.spec.ts`'s.
 */
describe('GardeningRoutinesPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(
    opts: { routines?: RoutineView[]; graphs?: GraphSummaryView[]; url?: string; mobile?: boolean } = {},
  ) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    stub = stubHubClient({
      'GET /api/routines': () => routines,
      'GET /api/graphs': () => graphs,
    });
    await TestBed.configureTestingModule({
      imports: [TestRoutinesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride(opts.mobile ? 'mobile' : 'desktop');
    const fixture = TestBed.createComponent(TestRoutinesHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/routines');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('opens the first row on the bare route, beside its detail pane', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="routine-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('highlights the row the child route names', async () => {
    const { el } = await render({ url: '/gardening/routines/nightly' });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('lists the routine by its display name, keyed and highlighted by its slug', async () => {
    const { el } = await render({ url: '/gardening/routines/nightly' });

    const row = el.querySelector('[data-testid="gardening-routine-row-nightly"]');
    expect(row?.querySelector('.rt-name')?.textContent).toBe('Nightly sweep');
    expect(row?.classList.contains('selected')).toBe(true);
  });

  it('highlights no row for a route naming the display name rather than the slug', async () => {
    const { el } = await render({ url: '/gardening/routines/Nightly%20sweep', mobile: true });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('keeps an unknown mobile routine route on its detail screen without a stale highlight', async () => {
    const { el } = await render({ url: '/gardening/routines/ghost', mobile: true });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
    expect(el.querySelector('[data-testid="gardening-routines-back"]')).toBeTruthy();
  });

  it('marks a routine whose graph has no effective mint as blocked in the list', async () => {
    const { el } = await render({ graphs: [] });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).toContain('blocked');
  });

  it('marks a retired routine in the list', async () => {
    const { el } = await render({ routines: [{ ...ROUTINE, retired: true }] });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).toContain('retired');
  });

  it('navigates to the routine route when a routine row is picked', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-row-nightly"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines/nightly');
  });

  it('renders its own empty state with no routines declared', async () => {
    const { el } = await render({ routines: [] });

    expect(el.querySelector('[data-testid="gardening-routines-empty"]')?.textContent).toContain(
      'No routines yet.',
    );
  });

  it('drills into a routine and back to the list in mobile mode', async () => {
    const { fixture, router, el } = await render({ mobile: true });

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-row-nightly"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines/nightly');
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routines-back"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines');
  });
});
