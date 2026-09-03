import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningFindingsPage } from './gardening-findings-page';

const ROUTINES = [
  { routine_id: 'rt_1', name: 'nightly', graph_name: 'sweep', default_scope_slug: 'blizzard', created_at: '2026-01-01T00:00:00Z' },
  { routine_id: 'rt_2', name: 'weekly', graph_name: 'sweep', default_scope_slug: 'web', created_at: '2026-01-01T00:00:00Z' },
];

const SCOPES = [
  { slug: 'blizzard', description: 'the blizzard repo', created_at: '2026-01-01T00:00:00Z' },
  { slug: 'web', description: 'the web workspace', created_at: '2026-01-01T00:00:00Z' },
];

function findingFixture(overrides: { state: string } & Record<string, unknown>) {
  return {
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    observed_count: 1,
    last_seen_at: '2026-01-05T00:00:00Z',
    introduced: '4ba7ef06d',
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
const FINDING_GONE_CONFIRMED = findingFixture({
  finding_id: 'fnd_14',
  class: 'unused-import',
  locus: 'e.py:5',
  summary: 'summary e',
  state: 'gone-confirmed',
  note: 'confirmed gone',
});

const BUCKET = [FINDING_LIVE, FINDING_GONE, FINDING_RESOLVED_1, FINDING_GONE_CONFIRMED];

/** Stands in for `GardeningFindingDetail`, whose own behavior is
 * `gardening-finding-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-finding-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="finding-detail-stub"></span>',
})
class TestFindingDetail {}

@Component({
  selector: 'app-test-findings-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestFindingsHost {}

/** The real route table's own shape for this tab (`app.routes.ts`), driven by the
 * real router — the filters under test live in the URL, and the selection on a
 * child route, so a stubbed `ActivatedRoute` could prove neither. */
const routes: Routes = [
  {
    path: 'gardening/findings',
    component: GardeningFindingsPage,
    children: [
      { path: '', component: TestFindingDetail },
      { path: ':findingId', component: TestFindingDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/findings` list container — the triage list, its four
 * filters, and the agreement it keeps between those filters and the finding the
 * URL names. The detail pane beside it is `gardening-finding-detail.spec.ts`'s.
 *
 * All four filters (routine, scope, class, state) render inline as
 * `fleet-kit-chips` — no accordion, no other gardening tab collapses its filters —
 * and all four live in the query string, which is what lets a pick survive a row
 * click and a filtered bucket be a shareable link.
 */
describe('GardeningFindingsPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function mount(opts: { url?: string; routeOverride?: (method: string, path: string) => unknown } = {}) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/findings') return [];
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return ROUTINES;
      if (method === 'GET' && path === '/api/scopes') return SCOPES;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestFindingsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestFindingsHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/findings');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  /** Every fixture that needs rows in the bucket answers `/api/findings` with them. */
  const withBucket = (method: string, path: string) =>
    method === 'GET' && path === '/api/findings' ? BUCKET : undefined;

  function pressed(el: HTMLElement, testid: string): string | null | undefined {
    return el.querySelector(`[data-testid="${testid}"]`)?.getAttribute('aria-pressed');
  }

  it('renders all four filters — routine, scope, class, state — as chips in one row, with no accordion to expand', async () => {
    const { el } = await mount({ routeOverride: withBucket });

    expect(el.querySelector('[data-testid="accordion-section-head"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-findings-routine-item-nightly"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-findings-scope-item-blizzard"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-class-all"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-state-all"]')).toBeTruthy();
    // Neither routine nor scope carries an "All" option — the bucket read always
    // needs a concrete pair.
    expect(el.querySelector('[data-testid="gardening-findings-routine-all"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-findings-scope-all"]')).toBeNull();
  });

  it('keeps a detail pane mounted on the bare route, with no row highlighted', async () => {
    const { el } = await mount({ routeOverride: withBucket });

    expect(el.querySelector('[data-testid="finding-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('highlights the row the child route names', async () => {
    const { el } = await mount({ url: '/gardening/findings/fnd_10', routeOverride: withBucket });

    expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('navigates to gardening/findings/:findingId when a finding row is picked', async () => {
    const { fixture, router, el } = await mount({ routeOverride: withBucket });

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-finding-row-fnd_10"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/findings/fnd_10');
  });

  it('keeps every active filter through a row pick, rather than resetting it', async () => {
    const { fixture, router, el } = await mount({
      url: '/gardening/findings?routine=weekly&scope=web&class=unused-import&state=gone',
      routeOverride: withBucket,
    });
    expect(pressed(el, 'gardening-findings-routine-item-weekly')).toBe('true');
    expect(pressed(el, 'gardening-finding-class-item-unused-import')).toBe('true');

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-finding-row-fnd_11"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/findings/fnd_11?routine=weekly&scope=web&class=unused-import&state=gone');
    expect(pressed(el, 'gardening-findings-routine-item-weekly')).toBe('true');
    expect(pressed(el, 'gardening-findings-scope-item-web')).toBe('true');
    expect(pressed(el, 'gardening-finding-class-item-unused-import')).toBe('true');
    expect(pressed(el, 'gardening-finding-state-item-gone')).toBe('true');
  });

  describe('the findings triage bucket', () => {
    it("seeds the bucket's routine/scope from the first fetched routine's own name and default scope, showing both chips already selected with no interaction and no run list mounted anywhere in this tab", async () => {
      const { el } = await mount({ routeOverride: withBucket });

      expect(pressed(el, 'gardening-findings-routine-item-nightly')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-item-blizzard')).toBe('true');

      const live = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      const gone = el.querySelector('[data-testid="gardening-finding-row-fnd_11"]');
      const resolved = el.querySelector('[data-testid="gardening-finding-row-fnd_12"]');
      expect(live).toBeTruthy();
      expect(gone?.querySelector('.fl-body--gone')).toBeTruthy();
      expect(resolved?.querySelector('.fl-body--exited')).toBeTruthy();
    });

    it('takes its routine/scope pair from the URL over the seed, so a filtered bucket is a shareable link', async () => {
      const { el } = await mount({
        url: '/gardening/findings?routine=weekly&scope=web',
        routeOverride: withBucket,
      });

      expect(pressed(el, 'gardening-findings-routine-item-weekly')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-item-web')).toBe('true');
      expect(pressed(el, 'gardening-findings-routine-item-nightly')).toBe('false');
    });

    it("renders the bucket's own rest state while no routines exist and no explicit pick has been made", async () => {
      const { fixture, el } = await mount({
        routeOverride: (method, path) => {
          if (method === 'GET' && path === '/api/routines') return [];
          if (method === 'GET' && path === '/api/findings') return BUCKET;
          return undefined;
        },
      });

      const list = fixture.debugElement.query(By.css('fleet-finding-list'));
      expect(list.componentInstance.state()).toBe('empty');
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')).toBeNull();
    });

    it('narrows the rendered rows via the class and state filters, naming each in the URL', async () => {
      const { fixture, router, el } = await mount({ routeOverride: withBucket });

      el.querySelector<HTMLElement>('[data-testid="gardening-finding-class-item-unused-import"]')!.click();
      await settle(fixture);

      expect(router.url).toBe('/gardening/findings?class=unused-import');
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_11"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_14"]')).toBeTruthy();

      el.querySelector<HTMLElement>('[data-testid="gardening-finding-state-item-gone"]')!.click();
      await settle(fixture);

      expect(router.url).toBe('/gardening/findings?class=unused-import&state=gone');
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_11"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_14"]')).toBeNull();
    });

    it('re-seeds an unchosen scope off the newly picked routine, rather than stapling the old default on (F2)', async () => {
      const { fixture, router, el } = await mount({ routeOverride: withBucket });
      expect(pressed(el, 'gardening-findings-routine-item-nightly')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-item-blizzard')).toBe('true');

      el.querySelector<HTMLElement>('[data-testid="gardening-findings-routine-item-weekly"]')!.click();
      await settle(fixture);

      // Scope was never chosen — it was sitting on nightly's default. The pick
      // leaves it unnamed so it re-seeds off weekly's own 'web', the pairing a run
      // of weekly would have used, instead of carrying nightly's 'blizzard' over.
      expect(router.url).toBe('/gardening/findings?routine=weekly');
      expect(pressed(el, 'gardening-findings-scope-item-web')).toBe('true');
    });

    it('carries an explicitly chosen scope across a routine pick (F2)', async () => {
      const { fixture, router, el } = await mount({
        url: '/gardening/findings?scope=blizzard',
        routeOverride: withBucket,
      });
      expect(pressed(el, 'gardening-findings-scope-item-blizzard')).toBe('true');

      el.querySelector<HTMLElement>('[data-testid="gardening-findings-routine-item-weekly"]')!.click();
      await settle(fixture);

      // Named in the URL, 'blizzard' is a choice rather than a seed, so it survives
      // the pick even though weekly's own default is 'web'.
      expect(router.url).toBe('/gardening/findings?scope=blizzard&routine=weekly');
      expect(pressed(el, 'gardening-findings-scope-item-blizzard')).toBe('true');
    });

    it("resolves scope off the routine the URL names, not the routine list's first row", async () => {
      const { el } = await mount({ url: '/gardening/findings?routine=weekly', routeOverride: withBucket });

      // A link naming only a routine is the symmetric case to naming neither: the
      // scope seed follows the routine in effect, so weekly pairs with 'web'.
      expect(pressed(el, 'gardening-findings-routine-item-weekly')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-item-web')).toBe('true');
    });

    it('clears the class and state filters on a routine or scope pick (F5)', async () => {
      const { fixture, el } = await mount({
        url: '/gardening/findings?class=unused-import&state=gone',
        routeOverride: withBucket,
      });
      expect(pressed(el, 'gardening-finding-class-item-unused-import')).toBe('true');
      expect(pressed(el, 'gardening-finding-state-item-gone')).toBe('true');

      el.querySelector<HTMLElement>('[data-testid="gardening-findings-routine-item-weekly"]')!.click();
      await settle(fixture);

      expect(pressed(el, 'gardening-finding-class-all')).toBe('true');
      expect(pressed(el, 'gardening-finding-state-all')).toBe('true');
    });

    it('clears a selected finding that a filter change removes from the bucket, keeping the filter itself', async () => {
      const { fixture, router, el } = await mount({
        url: '/gardening/findings/fnd_10',
        routeOverride: withBucket,
      });
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')?.classList.contains('selected')).toBe(
        true,
      );

      // fnd_10 is 'stale-docstring' — this class pick excludes it from the bucket's
      // filtered rows without touching the routine/scope query itself.
      el.querySelector<HTMLElement>('[data-testid="gardening-finding-class-item-unused-import"]')!.click();
      await settle(fixture);

      expect(router.url).toBe('/gardening/findings?class=unused-import');
    });

    it('does not clear a selection while the bucket read triggered by a routine/scope pick is still pending', async () => {
      const { fixture, router } = await mount({
        url: '/gardening/findings/fnd_10',
        routeOverride: withBucket,
      });

      // Landing on a new routine starts a brand-new bucket query — pending until
      // the stubbed fetch's own promise chain resolves. Rendered synchronously,
      // with no `await` in between, so no microtask has run yet: the row list
      // reads empty right now (no data for the new query key), which is exactly
      // the state a naive "id not in rows" check would misread as "filtered out".
      await router.navigateByUrl('/gardening/findings/fnd_10?routine=weekly&scope=web');
      fixture.detectChanges();

      expect(router.url).toContain('fnd_10');

      // Once the read settles the stub answers `/api/findings` the same way
      // regardless of the routine/scope query params, so fnd_10 is still present —
      // the selection survives, proving the pending window never fired a
      // premature clear.
      await settle(fixture);
      expect(router.url).toBe('/gardening/findings/fnd_10?routine=weekly&scope=web');
    });
  });
});
