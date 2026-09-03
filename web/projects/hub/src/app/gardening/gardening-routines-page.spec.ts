import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient, type RequestClientStub } from 'fleet/testing';

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
      { path: ':routineName', component: TestRoutineDetail },
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

  async function render(opts: { routines?: readonly unknown[]; graphs?: readonly unknown[]; url?: string } = {}) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines') return routines;
      if (method === 'GET' && path === '/api/graphs') return graphs;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestRoutinesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestRoutinesHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/routines');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('keeps a detail pane mounted on the bare route, with no row highlighted', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="routine-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('highlights the row the child route names', async () => {
    const { el } = await render({ url: '/gardening/routines/nightly' });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('a routineName param naming an unknown routine highlights nothing, rather than a stale row', async () => {
    const { el } = await render({ url: '/gardening/routines/ghost' });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('marks a routine whose graph has no effective mint as blocked in the list', async () => {
    const { el } = await render({ graphs: [] });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).toContain('blocked');
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
      'tending begins when there is growth worth pruning',
    );
  });
});
