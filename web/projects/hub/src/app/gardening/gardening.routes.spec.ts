import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { routes } from '../app.routes';

/**
 * The `/gardening` subtree in the real route table (`app.routes.ts`, blizzard#397) —
 * proves the five top-level children resolve, the bare parent path redirects to
 * `scopes` (the leftmost tab), and each of the five bare/param pairs (`scopes`,
 * `routines`, `runs`, `findings`, `proposals`) actually mounts its page and selects
 * the right detail — through the actual router rather than by mounting a page component
 * directly (that's each page's own spec). None of the five shares a selection with
 * a sibling any more — each tab's own list has only one kind of thing to select. A
 * bare `<router-outlet>` host stands in for `App`'s heavier one (auth session gate,
 * live-update spine) — irrelevant to whether this route subtree itself is wired
 * correctly.
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
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestGardeningRouteHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
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
      await router.navigateByUrl(`/gardening/${tab}`);
      await settle(fixture, 8);

      expect(router.url, `navigating to ${tab} from the previous tab did not land`).toBe(`/gardening/${tab}`);
      expect(
        (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="gardening-${tab}-list"]`) ??
          (fixture.nativeElement as HTMLElement).textContent,
        `${tab} landed in the URL but rendered nothing`,
      ).toBeTruthy();
    }
  });

  it('redirects the bare /gardening path to /gardening/scopes, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening');
    await settle(fixture);

    expect(router.url).toBe('/gardening/scopes');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-scopes-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeTruthy();
  });

  it('resolves /gardening/scopes/:scopeSlug to the scope detail', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/scopes') {
        return [{ slug: 'blizzard', description: 'the blizzard monorepo', retired: false, created_at: '2026-01-01T00:00:00Z' }];
      }
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/scopes/blizzard');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-scope-panel"]')?.textContent).toContain('blizzard');
  });

  it('resolves /gardening/routines to its own sub-tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/routines');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routines-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-routines"].active')).toBeTruthy();
  });

  it('resolves /gardening/routines/:routineName to the routine detail', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') {
        return [
          {
            routine_id: 'rtn_1',
            name: 'nightly',
            graph_name: 'garden-routine',
            default_scope_slug: 'blizzard',
            default_model: [],
            default_effort: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ];
      }
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/routines/trend') {
        return { routine_name: 'nightly', since: '2026-01-01T00:00:00Z', until: '2026-01-29T00:00:00Z', period_days: 7, periods: [], age: { boundary: '2026-01-01T00:00:00Z', recent: 0, older: 0, unattributed: 0 } };
      }
      if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') {
        return { routine_name: 'nightly', since: '2026-01-01T00:00:00Z', until: '2026-01-29T00:00:00Z', last_swept: [], measurements: [] };
      }
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/routines/nightly');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routine-record"]')?.textContent).toContain('nightly');
  });

  it('resolves /gardening/runs to its own sub-tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/runs');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-runs-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-runs"].active')).toBeTruthy();
  });

  it('resolves /gardening/runs/:chunkId to the run detail', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/runs/ch_1') {
        return {
          chunk_id: 'ch_1',
          routine_name: 'nightly',
          scope_slug: 'blizzard',
          mode: 'full',
          outcome: 'done',
          escalation: null,
          sets: [],
        };
      }
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/runs/ch_1');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-run-delta"]')).toBeTruthy();
  });

  it('resolves /gardening/findings to its own sub-tab, selecting nothing', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/findings');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-findings-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-findings"].active')).toBeTruthy();
  });

  it('resolves /gardening/findings/:findingId to the finding detail', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/findings/fnd_1') {
        return {
          finding_id: 'fnd_1',
          routine_name: 'nightly',
          scope_slug: 'blizzard',
          class: 'stale-docstring',
          locus: 'a.py:1',
          summary: 'summary a',
          state: 'live',
          live: true,
          introduced: null,
          last_seen_at: '2026-01-05T00:00:00Z',
          observed_count: 1,
          note: null,
        };
      }
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/findings/fnd_1');
    await settle(fixture, 12);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-finding-panel"]')).toBeTruthy();
  });

  it('resolves /gardening/proposals to its own sub-tab, deep-linkable on its own', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/proposals');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-proposals"].active')).toBeTruthy();
  });

  it('resolves /gardening/proposals/:proposalId to that proposal, not the first row of the docket', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') {
        return [
          {
            proposal_id: 'gprop_1',
            routine_name: 'comments',
            class: 'fix-the-source',
            title: 'Author a docstring standard',
            body: 'Seventeen modules narrate their own change history.',
            created_at: '2026-01-01T00:00:00Z',
            findings: [],
            closure: null,
          },
          {
            proposal_id: 'gprop_2',
            routine_name: 'comments',
            class: 'remediate',
            title: 'Delete the dead helper',
            body: 'Nothing calls it.',
            created_at: '2026-01-02T00:00:00Z',
            findings: [],
            closure: null,
          },
        ];
      }
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      if (method === 'GET' && path === '/api/runs') return [];
      if (method === 'GET' && path === '/api/scopes') return [];
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      return {};
    });
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/proposals/gprop_2');
    await settle(fixture, 12);

    // The param, not the docket's own first-row fallback, drives the pane — and the
    // list stays mounted beside it across the bare/param pair.
    expect(router.url).toBe('/gardening/proposals/gprop_2');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_2"]')?.classList).toContain('selected');
    expect(el.querySelector('[data-testid="gardening-proposal-case"]')?.textContent).toContain(
      'Delete the dead helper',
    );
  });
});
