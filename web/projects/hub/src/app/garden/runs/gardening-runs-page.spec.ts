import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { FLEET_CLOCK, ViewportService } from 'fleet';
import {
  deliveredSetView,
  type RequestClientStub,
  runEscalationView,
  runRowView,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { GardeningRunsPage } from './gardening-runs-page';

const RUN_ROW = runRowView({
  chunk_id: 'ch_1',
  minted_at: '2026-01-10T00:00:00Z',
  delivered: [
    deliveredSetView({ finding_set_id: 'fins_1', revisions: { blizzard: 'abc123' }, measurement: '3 findings', added_count: 1, observed_count: 11, gone_count: 0 }),
    deliveredSetView({ finding_set_id: 'fins_2', revisions: { blizzard: 'def456' }, added_count: 0, observed_count: 0, gone_count: 2 }),
  ],
});

const ESCALATED_RUN_ROW = runRowView({
  chunk_id: 'ch_2',
  scope_slug: 'web',
  mode: 'delta',
  minted_at: '2026-01-11T00:00:00Z',
  outcome: 'needs_human',
  escalation: runEscalationView({ node_name: 'survey', takeover_command: 'blizzard hub chunk takeover ch_2' }),
});

/** Stands in for `GardeningRunDetail`, whose own behavior is
 * `gardening-run-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-run-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="run-detail-stub"></span>',
})
class TestRunDetail {}

@Component({
  selector: 'app-test-runs-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestRunsHost {}

/** The real route table's own shape for this tab (`app.routes.ts`), driven by the
 * real router — what is under test *is* that the parent reads its child's param
 * and survives the pick. */
const routes: Routes = [
  {
    path: 'gardening/runs',
    component: GardeningRunsPage,
    children: [
      { path: '', component: TestRunDetail },
      { path: ':chunkId', component: TestRunDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/runs` list container — the run list, its selection
 * highlight, and the navigation a row pick performs, including summing each row's
 * finding counts across its delivered sets. The delta pane beside it is
 * `gardening-run-detail.spec.ts`'s.
 */
describe('GardeningRunsPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function mount(
    opts: { url?: string; routes?: Parameters<typeof stubHubClient>[0]; mobile?: boolean } = {},
  ) {
    stub = stubHubClient({
      'GET /api/runs': () => [RUN_ROW, ESCALATED_RUN_ROW],
      'GET /api/routines': () => [],
      ...opts.routes,
    });
    await TestBed.configureTestingModule({
      imports: [TestRunsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride(opts.mobile ? 'mobile' : 'desktop');
    const fixture = TestBed.createComponent(TestRunsHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/runs');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('renders its own empty state with no runs recorded', async () => {
    const { el } = await mount({
      routes: { 'GET /api/runs': () => [] },
    });

    expect(el.querySelector('[data-testid="gardening-runs-empty"]')?.textContent).toContain(
      'No runs yet.',
    );
  });

  it('lists every run with its routine, scope, and mode, summing its delivered sets’ counts into one triple', async () => {
    const { el } = await mount();

    const row = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    expect(row?.textContent).toContain('nightly/blizzard');
    expect(row?.textContent).toContain('Full');
    expect(row?.querySelector('[data-testid="rl-counts"]')?.textContent).toBe('+1 / 11 / -2');
  });

  it('cuts the runs read’s window from FLEET_CLOCK, 28 days back', async () => {
    const nowMs = Date.UTC(2026, 6, 16, 12, 0, 0);
    TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => nowMs });
    await mount();

    const since = new Date(nowMs - 28 * 24 * 60 * 60 * 1000).toISOString();
    expect(stub.forRoute('/api/runs', 'GET').map((r) => r.search)).toContain(`?since=${encodeURIComponent(since)}`);
  });

  it('renders nothing for a row’s counts when it delivered no sets', async () => {
    const { el } = await mount();

    expect(el.querySelector('[data-testid="gardening-run-row-ch_2"] [data-testid="rl-counts"]')).toBeNull();
  });

  it('renders an escalated row distinctly from a normal row', async () => {
    const { el } = await mount();

    expect(el.querySelector('[data-testid="gardening-run-row-ch_1"] .rl-body--escalated')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-run-row-ch_2"] .rl-body--escalated')).toBeTruthy();
  });

  it('opens the first row on the bare route, beside its detail pane', async () => {
    const { el } = await mount();

    expect(el.querySelector('[data-testid="run-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-run-row-ch_1"]')?.classList.contains('selected')).toBe(true);
  });

  it('highlights the row the child route names', async () => {
    const { el } = await mount({ url: '/gardening/runs/ch_1' });

    expect(el.querySelector('[data-testid="gardening-run-row-ch_1"]')?.classList.contains('selected')).toBe(true);
  });

  it('navigates to gardening/runs/:chunkId when a run is picked', async () => {
    const { fixture, router, el } = await mount();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-run-row-ch_1"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/runs/ch_1');
  });

  it('drills into a run and back to the list in mobile mode', async () => {
    const { fixture, router, el } = await mount({ mobile: true });

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-run-row-ch_1"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/runs/ch_1');
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-runs-back"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/runs');
  });

  it('resolves a run-list read failure to the error state', async () => {
    const { fixture, el } = await mount({
      routes: { 'GET /api/runs': () => stubError(500, { detail: 'the store is down' }) },
    });

    const list = fixture.debugElement.query(By.css('app-run-list'));
    expect(list.componentInstance.state()).toBe('error');
    expect(el.textContent).toContain('UNAVAILABLE');
  });
});
