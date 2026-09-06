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
/** A second routine/scope, distinct from the other three fixtures' `nightly`/
 * `blizzard` — the disambiguation markup (blizzard#486) only means something once a
 * bucket genuinely mixes rows from more than one of each. */
const FINDING_OTHER_ROUTINE = findingFixture({
  finding_id: 'fnd_20',
  class: 'unused-import',
  locus: 'w.py:1',
  summary: 'summary w',
  state: 'live',
  routine_name: 'weekly',
  scope_slug: 'web',
});

const BUCKET = [FINDING_LIVE, FINDING_GONE, FINDING_RESOLVED_1, FINDING_GONE_CONFIRMED, FINDING_OTHER_ROUTINE];

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
    // Every filter now carries an "All" option (blizzard#486) — the bucket read no
    // longer requires a concrete routine/scope pair.
    expect(el.querySelector('[data-testid="gardening-findings-routine-all"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-findings-scope-all"]')).toBeTruthy();
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
    it('rests on every routine and every scope with no query params — both "All" chips selected, the bucket read firing with neither named', async () => {
      const { el } = await mount({ routeOverride: withBucket });

      expect(pressed(el, 'gardening-findings-routine-all')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-all')).toBe('true');

      // The bucket read fires immediately, no seeded routine/scope required, and
      // renders rows from more than one routine and scope at once.
      const nightly = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      const weekly = el.querySelector('[data-testid="gardening-finding-row-fnd_20"]');
      expect(nightly).toBeTruthy();
      expect(weekly).toBeTruthy();
      const gone = el.querySelector('[data-testid="gardening-finding-row-fnd_11"]');
      const resolved = el.querySelector('[data-testid="gardening-finding-row-fnd_12"]');
      expect(gone?.querySelector('.fl-body--gone')).toBeTruthy();
      expect(resolved?.querySelector('.fl-body--exited')).toBeTruthy();
    });

    it('reaches a scope literally named "all" through its own chip, distinct from the "All scopes" sentinel (review:F1)', async () => {
      // `stubRequestClient`'s own `CapturedRequest` drops each request's query
      // string down to a bare path — this local fetch stub keeps the full URL
      // alongside it (`finding.query.spec.ts`'s own `stubFetchCapturingUrl`
      // shape), needed here to prove `scope=all` genuinely rides the bucket
      // read rather than being read as the "every scope" sentinel and omitted.
      const SCOPE_ALL = { slug: 'all', description: 'a scope literally named all', created_at: '2026-01-01T00:00:00Z' };
      const FINDING_IN_SCOPE_ALL = findingFixture({
        finding_id: 'fnd_30',
        class: 'stale-docstring',
        locus: 'z.py:1',
        summary: 'summary z',
        state: 'live',
        scope_slug: 'all',
      });
      const urls: string[] = [];
      const previousFetch = globalThis.fetch;
      const fakeFetch = async (input: Request): Promise<Response> => {
        const url = new URL(input.url);
        if (url.pathname === '/api/findings') urls.push(input.url);
        const method = input.method.toUpperCase();
        let body: unknown = {};
        if (method === 'GET' && url.pathname === '/api/me') body = OPERATOR_ME_RESPONSE;
        else if (method === 'GET' && url.pathname === '/api/findings') body = [...BUCKET, FINDING_IN_SCOPE_ALL];
        else if (method === 'GET' && url.pathname === '/api/garden-proposals') body = [];
        else if (method === 'GET' && url.pathname === '/api/routines') body = ROUTINES;
        else if (method === 'GET' && url.pathname === '/api/scopes') body = [...SCOPES, SCOPE_ALL];
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      };
      hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });
      try {
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
        await router.navigateByUrl('/gardening/findings');
        await settle(fixture, 12);
        const el = fixture.nativeElement as HTMLElement;

        el.querySelector<HTMLButtonElement>('[data-testid="gardening-findings-scope-item-all"]')!.click();
        await settle(fixture);

        expect(router.url).toBe('/gardening/findings?scope=all');
        expect(pressed(el, 'gardening-findings-scope-item-all')).toBe('true');
        expect(pressed(el, 'gardening-findings-scope-all')).toBe('false');
        expect(el.querySelector('[data-testid="gardening-finding-row-fnd_30"]')).toBeTruthy();

        const lastFindingsUrl = urls.at(-1)!;
        expect(new URL(lastFindingsUrl).searchParams.get('scope')).toBe('all');
      } finally {
        hubClient.setConfig({ baseUrl: '', fetch: previousFetch });
      }
    });

    it('takes an explicit routine/scope pair from the URL, so a filtered bucket is a shareable link', async () => {
      const { el } = await mount({
        url: '/gardening/findings?routine=weekly&scope=web',
        routeOverride: withBucket,
      });

      expect(pressed(el, 'gardening-findings-routine-item-weekly')).toBe('true');
      expect(pressed(el, 'gardening-findings-scope-item-web')).toBe('true');
      expect(pressed(el, 'gardening-findings-routine-all')).toBe('false');
      expect(pressed(el, 'gardening-findings-scope-all')).toBe('false');
    });

    it("renders the bucket's own empty rest state when the read resolves with no rows", async () => {
      const { fixture, el } = await mount({
        routeOverride: (method, path) => {
          if (method === 'GET' && path === '/api/findings') return [];
          return undefined;
        },
      });

      const list = fixture.debugElement.query(By.css('fleet-finding-list'));
      expect(list.componentInstance.state()).toBe('empty');
      expect(el.querySelector('[data-testid="gardening-finding-row-fnd_10"]')).toBeNull();
    });

    it("shows each row's own routine and scope while both dimensions are unnamed, so a widened bucket disambiguates itself", async () => {
      const { el } = await mount({ routeOverride: withBucket });

      const nightly = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      expect(nightly?.querySelector('.fl-routine')?.textContent?.trim()).toBe('nightly');
      expect(nightly?.querySelector('.fl-scope')?.textContent?.trim()).toBe('blizzard');

      const weekly = el.querySelector('[data-testid="gardening-finding-row-fnd_20"]');
      expect(weekly?.querySelector('.fl-routine')?.textContent?.trim()).toBe('weekly');
      expect(weekly?.querySelector('.fl-scope')?.textContent?.trim()).toBe('web');
    });

    it('omits the routine/scope markup once a concrete routine and scope are chosen', async () => {
      const { el } = await mount({
        url: '/gardening/findings?routine=nightly&scope=blizzard',
        routeOverride: withBucket,
      });

      const nightly = el.querySelector('[data-testid="gardening-finding-row-fnd_10"]');
      expect(nightly?.querySelector('.fl-routine')).toBeNull();
      expect(nightly?.querySelector('.fl-scope')).toBeNull();
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
