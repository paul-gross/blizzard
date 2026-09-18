import { ChangeDetectionStrategy, Component, EnvironmentInjector, provideZonelessChangeDetection, runInInjectionContext } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, injectAcceptGardenProposalMutation, injectPassGardenProposalMutation, type MeResponse, ViewportService } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { GardeningProposalsPage } from './gardening-proposals-page';

/** A read-only identity — every permission `OPERATOR_ME_RESPONSE` carries except
 * `chunk:control` — the default for tests unconcerned with the Pass/Accept gate. */
const VIEWER_ME_RESPONSE: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'chunk:control'),
};

const WAITING_A = {
  proposal_id: 'gp_1',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Author a docstring standard',
  body: 'Seventeen modules narrate their own change history.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fin_1', 'fin_2'],
  closure: null,
};

const WAITING_B = {
  proposal_id: 'gp_2',
  routine_name: 'comments',
  class: 'remediate',
  title: 'Delete the dead helper',
  body: 'Nothing calls it.',
  created_at: '2026-01-02T00:00:00Z',
  findings: ['fin_3'],
  closure: null,
};

const PASSED = {
  proposal_id: 'gp_3',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Rewrite the whole module',
  body: 'Too large for this pass.',
  created_at: '2026-01-03T00:00:00Z',
  findings: ['fin_4'],
  closure: {
    closure: 'passed',
    reason: 'not worth it yet',
    closed_by: 'u_1',
    closed_at: '2026-01-04T00:00:00Z',
    item_outcome: null,
    source: null,
    ref: null,
  },
};

const ARCHITECTURE_WAITING = {
  proposal_id: 'gp_4',
  routine_name: 'architecture',
  class: 'remediate',
  title: 'Extract the shared seam',
  body: 'Two modules duplicate the same adapter.',
  created_at: '2026-01-05T00:00:00Z',
  findings: ['fin_5'],
  closure: null,
};

function findingFixture(findingId: string) {
  return {
    finding_id: findingId,
    routine_name: 'comments',
    scope_slug: 'blizzard',
    class: 'stale-docstring',
    locus: `src/${findingId}.py:1`,
    summary: `summary for ${findingId}`,
    state: 'live',
    live: true,
    observed_count: 1,
    last_seen_at: '2026-01-01T00:00:00Z',
  };
}

/** Stands in for `GardeningProposalDetail`, whose own behavior is
 * `gardening-proposal-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-proposal-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="proposal-detail-stub"></span>',
})
class TestProposalDetail {}

@Component({
  selector: 'app-test-proposals-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestProposalsHost {}

/** The real route table's own shape for this tab (`app.routes.ts`), driven by the
 * real router — the filters under test live in the URL, and the docket drives its
 * own selection through real navigations, so a stubbed `Router` could prove
 * neither. */
const routes: Routes = [
  {
    path: 'gardening/proposals',
    component: GardeningProposalsPage,
    children: [
      { path: '', component: TestProposalDetail },
      { path: ':proposalId', component: TestProposalDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/proposals` docket — the list, its waiting/class
 * filters, and the selection it keeps in agreement with them. The detail pane
 * beside it is `gardening-proposal-detail.spec.ts`'s.
 */
describe('GardeningProposalsPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(
    proposals: readonly unknown[] = [WAITING_A, WAITING_B, PASSED],
    me: MeResponse = VIEWER_ME_RESPONSE,
    url = '/gardening/proposals',
    mobile = false,
    routeOverride?: (method: string, path: string) => unknown,
  ) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/garden-proposals') return { proposals, next_cursor: null };
      if (method === 'GET' && path === '/api/me') return me;
      if (method === 'GET' && path.startsWith('/api/findings/')) return findingFixture(path.split('/').pop()!);
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestProposalsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride(mobile ? 'mobile' : 'desktop');
    const fixture = TestBed.createComponent(TestProposalsHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    await settle(fixture, 8);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('lists every waiting proposal by default, and routes the bare path to the first row', async () => {
    const { router, el } = await render();

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_3"]')).toBeNull();
    expect(router.url).toBe('/gardening/proposals/gp_1');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).toContain('selected');
  });

  it('rests on the list, drills into a proposal, and returns in mobile mode', async () => {
    const { fixture, router, el } = await render(
      [WAITING_A, WAITING_B, PASSED],
      VIEWER_ME_RESPONSE,
      '/gardening/proposals?show=all',
      true,
    );

    const page = el.querySelector('app-gardening-proposals-page')!;
    expect(router.url).toBe('/gardening/proposals?show=all');
    expect(page.classList).toContain('mobile');
    expect(page.classList).not.toContain('detail-open');

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-row-gp_1"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals/gp_1?show=all');
    expect(page.classList).toContain('detail-open');
    el.querySelector<HTMLAnchorElement>('[data-testid="gardening-proposals-back"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals?show=all');
    expect(page.classList).not.toContain('detail-open');
  });

  it('leaves the bare route alone on an empty docket, rather than redirecting nowhere', async () => {
    const { router, el } = await render([]);

    expect(router.url).toBe('/gardening/proposals');
    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });

  it('keeps the proposal the route already names rather than snapping to the first row', async () => {
    const { router, el } = await render(
      [WAITING_A, WAITING_B, PASSED],
      VIEWER_ME_RESPONSE,
      '/gardening/proposals/gp_2',
    );

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')?.classList).toContain('selected');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).not.toContain('selected');
    // The deep link survives the window where the list read is still pending and
    // the filtered set therefore reads empty — nothing bounces it away before its
    // own data arrives.
    expect(router.url).toBe('/gardening/proposals/gp_2');
  });

  it('navigates to gardening/proposals/:proposalId when a row is picked', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-row-gp_2"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals/gp_2');
  });

  it('keeps both filters through a row pick, rather than resetting them', async () => {
    const { fixture, router, el } = await render(
      [WAITING_A, WAITING_B, PASSED],
      VIEWER_ME_RESPONSE,
      '/gardening/proposals?show=all&class=fix-the-source',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_3"]')).toBeTruthy();

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-row-gp_3"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals/gp_3?show=all&class=fix-the-source');
    expect(el.querySelector('[data-testid="gardening-proposal-filter-all"]')?.getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(
      el.querySelector('[data-testid="gardening-proposal-class-item-fix-the-source"]')?.getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('moves a routed proposal a filter change excludes onto the first row still in the set', async () => {
    const { fixture, router, el } = await render(
      [WAITING_A, WAITING_B, PASSED],
      VIEWER_ME_RESPONSE,
      '/gardening/proposals/gp_1',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).toContain('selected');

    // gp_1 is 'fix-the-source' — this class pick excludes it from the filtered set.
    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-class-item-remediate"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals/gp_2?class=remediate');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')?.classList).toContain('selected');
  });

  it('shows a passed proposal once the waiting filter is switched to all, and it stays reachable', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-filter-all"]')!.click();
    await settle(fixture);

    expect(router.url).toContain('show=all');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_3"]')).toBeTruthy();
  });

  it('derives the class chips from the fetched data, never a hardcoded list', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="gardening-proposal-class-all"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-item-fix-the-source"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-item-remediate"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-class-item-mechanize"]')).toBeNull();
  });

  it('filters the list down to one class', async () => {
    const { fixture, el } = await render();

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-class-item-remediate"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();
  });

  it("renders and filters by a deployment class literally named 'all' without colliding with the All-classes chip", async () => {
    const { fixture, el } = await render([{ ...WAITING_A, class: 'all' }, WAITING_B]);

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-class-item-all"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeNull();
  });

  it('renders the empty state only once the read resolves', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });

  it('derives the routine chips from the fetched data, defaulting to all routines', async () => {
    const { el } = await render([WAITING_A, ARCHITECTURE_WAITING]);

    expect(el.querySelector('[data-testid="gardening-proposal-routine-all"]')?.getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-routine-item-comments"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-routine-item-architecture"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_4"]')).toBeTruthy();
  });

  it('narrows the list down to one routine, riding the query string', async () => {
    const { router, fixture, el } = await render([WAITING_A, ARCHITECTURE_WAITING]);

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-routine-item-architecture"]')!.click();
    await settle(fixture);

    expect(router.url).toContain('routine=architecture');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_4"]')).toBeTruthy();
  });

  it('moves a routed proposal a routine change excludes onto the first row still in the set', async () => {
    const { fixture, router, el } = await render(
      [WAITING_A, ARCHITECTURE_WAITING],
      VIEWER_ME_RESPONSE,
      '/gardening/proposals/gp_1',
    );
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).toContain('selected');

    el.querySelector<HTMLElement>('[data-testid="gardening-proposal-routine-item-architecture"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/proposals/gp_4?routine=architecture');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_4"]')?.classList).toContain('selected');
  });

  describe('a proposal with a pending Pass/Accept drops from the waiting docket (Part B)', () => {
    /** `gardening-proposal-pass-dialog.ts`/`gardening-proposal-accept-dialog.ts` own
     * and fire these mutations, not this page — a `mutationKey`-scoped read is
     * exactly what lets the docket see another component's in-flight mutation
     * without owning it, so this fires it from the same root injector rather than
     * through `GardeningProposalsPage` itself (`board-page.spec.ts`'s own
     * `fireDeleteFrom` shape). */
    function firePassFrom(proposalId: string): { resolve: () => void } {
      const injector = TestBed.inject(EnvironmentInjector);
      const mutation = runInInjectionContext(injector, () => injectPassGardenProposalMutation());
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );
      mutation.mutate({ proposalId, reason: 'not worth it yet' });
      return { resolve: resolveInvalidate };
    }

    it('drops the row from the default waiting docket while Pass is pending, and restores it once it settles', async () => {
      const { fixture, el } = await render();
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();

      const { resolve } = firePassFrom('gp_1');
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeNull();

      resolve();
      await settle(fixture);
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    });

    it('restores the row when the pass is rejected', async () => {
      const { fixture, el } = await render(
        [WAITING_A, WAITING_B, PASSED],
        VIEWER_ME_RESPONSE,
        '/gardening/proposals',
        false,
        (method, path) =>
          method === 'POST' && path === '/api/garden-proposals/gp_1/pass'
            ? stubError(409, { detail: 'garden proposal gp_1 already carries a closure' })
            : undefined,
      );
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();

      const injector = TestBed.inject(EnvironmentInjector);
      const mutation = runInInjectionContext(injector, () => injectPassGardenProposalMutation());
      mutation.mutate({ proposalId: 'gp_1', reason: 'not worth it yet' });
      await settle(fixture);

      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    });

    it('drops the row while Accept is pending too — both closing verbs are total over "waiting"', async () => {
      const { fixture, el } = await render();
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();

      const injector = TestBed.inject(EnvironmentInjector);
      const mutation = runInInjectionContext(injector, () => injectAcceptGardenProposalMutation());
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );
      mutation.mutate({ proposalId: 'gp_2', mintWorkItem: true });
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeNull();

      resolveInvalidate();
      await settle(fixture);
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')).toBeTruthy();
    });

    it("does not drop the row under 'All', where a closed proposal is meant to stay reachable", async () => {
      const { fixture, el } = await render(
        [WAITING_A, WAITING_B, PASSED],
        VIEWER_ME_RESPONSE,
        '/gardening/proposals?show=all',
      );
      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();

      firePassFrom('gp_1');
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')).toBeTruthy();
    });
  });
});
