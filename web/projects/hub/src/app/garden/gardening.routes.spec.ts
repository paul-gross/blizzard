import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  gardenFindingDetailView,
  gardenFindingFactView,
  gardenProposalCountsView,
  gardenProposalView,
  gardenRoutineView,
  gardenScopeView,
  gardenSweepsView,
  gardenTrendView,
  runDeltaView,
  settle,
  stubHubClient,
} from 'fleet/testing';

import { provideProjectLens } from '../core/project-lens';
import { GARDENING_ROUTES } from './gardening.routes';

/** The lens every case opens the garden under. */
const PROJECT = 'blizzard';

/** The project's empty garden, every read the lensed tabs make answered, with `routes` declared over it. */
function stubGarden(routes: Parameters<typeof stubHubClient>[0] = {}) {
  return stubHubClient({
    'GET /api/projects/{project}/garden-proposals': () => ({ proposals: [], next_cursor: null }),
    'GET /api/projects/{project}/routines': () => [],
    'GET /api/graphs': () => [],
    'GET /api/projects/{project}/runs': () => [],
    'GET /api/projects/{project}/scopes': () => [],
    'GET /api/projects/{project}/findings': () => ({ findings: [], next_cursor: null }),
    'GET /api/projects/{project}/findings/classes': () => [],
    'GET /api/me': () => OPERATOR_ME_RESPONSE,
    ...routes,
  });
}

/**
 * The `/gardening` subtree (`gardening.routes.ts`, mounted at `gardening` under a project's lens, as the app's route
 * table mounts it; the doorway and the guards on All are `garden-lens.guard.spec.ts`'s) —
 * proves the five top-level children resolve, the bare parent path redirects to
 * `runs` (the leftmost tab), and each of the five bare/param pairs (`scopes`,
 * `routines`, `runs`, `findings`, `proposals`) actually mounts its page and selects
 * the right detail — through the actual router rather than by mounting a page component
 * directly (that's each page's own spec). A bare `<router-outlet>` host stands in for
 * `App`'s heavier one (auth session gate, live-update spine) — irrelevant to whether this route subtree itself is
 * wired correctly.
 */
@Component({
  selector: 'app-test-gardening-route-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestGardeningRouteHost {}

describe('the /gardening route subtree', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubGarden();
    await TestBed.configureTestingModule({
      imports: [TestGardeningRouteHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'p/:projectSlug', children: [{ path: 'gardening', children: GARDENING_ROUTES }] }]),
        provideProjectLens(),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('walks tab to tab without a dead navigation, the way the tab strip is actually used', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    // Every case above enters a tab cold, which resolves the whole route tree —
    // lazy children included — before any component is created. Clicking between
    // tabs does not: the incoming list page is constructed while its own
    // `loadComponent` child is still resolving, so its `ActivatedRoute.firstChild`
    // is in the tree with no `snapshot` on it yet (`route-state.ts`'s own
    // `injectChildRouteParam`). Reading through that unguarded threw in a field
    // initializer, which kills the component's construction and leaves the
    // navigation with no visible effect — the tab stops responding, with nothing
    // in the URL to show for it. Walking the strip is the only shape that catches it.
    for (const tab of ['scopes', 'routines', 'runs', 'findings', 'proposals', 'scopes', 'runs', 'findings']) {
      await router.navigateByUrl(`/p/${PROJECT}/gardening/${tab}`);
      await settle(fixture, 8);

      expect(router.url, `navigating to ${tab} from the previous tab did not land`).toBe(`/p/${PROJECT}/gardening/${tab}`);
      expect(
        (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="gardening-${tab}-list"]`) ??
          (fixture.nativeElement as HTMLElement).textContent,
        `${tab} landed in the URL but rendered nothing`,
      ).toBeTruthy();
    }
  });

  it('opens a project\'s bare gardening path on its proposals tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening');
    await settle(fixture);

    expect(router.url).toBe('/p/blizzard/gardening/proposals');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });

  it('resolves /gardening/scopes/:scopeSlug to the scope detail', async () => {
    stub.restore();
    stub = stubGarden({
      'GET /api/projects/{project}/scopes': () => [
        gardenScopeView({ slug: 'blizzard', description: 'the blizzard monorepo', created_at: '2026-01-01T00:00:00Z' }),
      ],
      'GET /api/projects/{project}/scopes/{slug}/routines': () => [],
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/scopes/blizzard');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-scope-panel"]')?.textContent).toContain('blizzard');
  });

  it('resolves /gardening/routines to its own sub-tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/routines');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routines-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-routines"].active')).toBeTruthy();
  });

  it('resolves /gardening/routines/:routineSlug to the routine detail by slug, showing its display name', async () => {
    stub.restore();
    stub = stubGarden({
      'GET /api/projects/{project}/routines': () => [
        gardenRoutineView({
          routine_id: 'rtn_1',
          name: 'Nightly sweep',
          slug: 'nightly',
          graph_name: 'garden-routine',
          default_scope_slug: 'blizzard',
          created_at: '2026-01-01T00:00:00Z',
        }),
      ],
      'GET /api/routines/trend': () => gardenTrendView(),
      'GET /api/routines/{routine_id}/sweeps': () => gardenSweepsView(),
      'GET /api/routines/{routine_id}/scopes': () => [],
      'GET /api/routines/proposal-counts': () => gardenProposalCountsView({ routine: 'nightly' }),
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/routines/nightly');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routine-record"] h3')?.textContent).toBe('Nightly sweep');
  });

  it('resolves /gardening/runs to its own sub-tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/runs');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-runs-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-runs"].active')).toBeTruthy();
  });

  it('resolves /gardening/runs/:chunkId to the run detail', async () => {
    stub.restore();
    stub = stubGarden({
      'GET /api/projects/{project}/runs/{chunk_id}': () => runDeltaView({ chunk_id: 'ch_1' }),
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/runs/ch_1');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-run-delta"]')).toBeTruthy();
  });

  it('resolves /gardening/findings to the findings list on the Findings tab', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/findings');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-findings-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-findings"].active')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-runs"].active')).toBeNull();
  });

  it('resolves /gardening/findings/:findingId to the finding detail', async () => {
    stub.restore();
    stub = stubGarden({
      'GET /api/projects/{project}/findings/classes': () => ['stale-docstring'],
      // The detail by id remains available even if no list page contains it.
      'GET /api/findings/{finding_id}': () =>
        gardenFindingDetailView({
          finding_id: 'fnd_1',
          routine_name: 'nightly',
          scope_slug: 'blizzard',
          class: 'stale-docstring',
          locus: 'a.py:1',
          summary: 'summary a',
          last_seen_at: '2026-01-05T00:00:00Z',
          observed_count: 1,
          facts: [gardenFindingFactView()],
        }),
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/findings/fnd_1');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-finding-panel"]')).toBeTruthy();
  });

  it('resolves /gardening/proposals to its own sub-tab, deep-linkable on its own', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/proposals');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-proposals"].active')).toBeTruthy();
  });

  it('resolves /gardening/proposals/:proposalId to that proposal, not the first row of the docket', async () => {
    stub.restore();
    stub = stubGarden({
      'GET /api/projects/{project}/garden-proposals': () => ({
        proposals: [
          gardenProposalView({
            proposal_id: 'gprop_1',
            routine_name: 'comments',
            class: 'fix-the-source',
            title: 'Author a docstring standard',
            body: 'Seventeen modules narrate their own change history.',
            created_at: '2026-01-01T00:00:00Z',
          }),
          gardenProposalView({
            proposal_id: 'gprop_2',
            routine_name: 'comments',
            class: 'remediate',
            title: 'Delete the dead helper',
            body: 'Nothing calls it.',
            created_at: '2026-01-02T00:00:00Z',
          }),
        ],
        next_cursor: null,
      }),
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/p/blizzard/gardening/proposals/gprop_2');
    await settle(fixture, 12);

    // The param, not the docket's own first-row fallback, drives the pane — and the
    // list stays mounted beside it across the bare/param pair.
    expect(router.url).toBe('/p/blizzard/gardening/proposals/gprop_2');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_2"]')?.classList).toContain('selected');
    expect(el.querySelector('[data-testid="gardening-proposal-case"]')?.textContent).toContain(
      'Delete the dead helper',
    );
  });

  it('reads every tab through the project\'s nested routes, and a scope by its slug only inside the project', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    for (const tab of ['scopes', 'routines', 'runs', 'findings', 'proposals']) {
      await router.navigateByUrl(`/p/${PROJECT}/gardening/${tab}`);
      await settle(fixture, 8);
    }

    const paths = stub.requests.map((request) => request.path);
    for (const nested of ['scopes', 'routines', 'runs', 'findings', 'garden-proposals']) {
      expect(paths, `the ${nested} read`).toContain(`/api/projects/${PROJECT}/${nested}`);
    }
    const unnestedGardenReads = paths.filter((path) =>
      /^\/api\/(scopes|routines|runs|findings|garden-proposals)(\/|$)/.test(path),
    );
    // Two tenant-wide lists set the project beside the others — the crumb menu's proposals waiting per project and the
    // scope detail's same-slug callout. No other garden list, and no record, is read outside the project.
    expect([...new Set(unnestedGardenReads)].sort()).toEqual(['/api/garden-proposals', '/api/scopes']);
  });
});
