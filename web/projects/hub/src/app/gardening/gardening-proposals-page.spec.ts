import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type MeResponse } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

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
  ) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return proposals;
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
});
