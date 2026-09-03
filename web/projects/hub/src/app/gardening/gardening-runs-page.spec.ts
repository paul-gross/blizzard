import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { GardeningRunsPage } from './gardening-runs-page';

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

  async function mount(opts: { url?: string; routeOverride?: (method: string, path: string) => unknown } = {}) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = opts.routeOverride?.(method, path);
      if (overridden !== undefined) return overridden;
      if (method === 'GET' && path === '/api/runs') return [RUN_ROW, ESCALATED_RUN_ROW];
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestRunsHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestRunsHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/runs');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('renders its own empty state with no runs recorded', async () => {
    const { el } = await mount({
      routeOverride: (method, path) => (method === 'GET' && path === '/api/runs' ? [] : undefined),
    });

    expect(el.querySelector('[data-testid="gardening-runs-empty"]')?.textContent).toContain(
      'tending begins when there is growth worth pruning',
    );
  });

  it('lists every run with its routine, scope, and mode, summing its delivered sets’ counts into one triple', async () => {
    const { el } = await mount();

    const row = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    expect(row?.textContent).toContain('nightly/blizzard');
    expect(row?.textContent).toContain('Full');
    expect(row?.querySelector('[data-testid="rl-counts"]')?.textContent).toBe('+1 / 11 / -2');
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

  it('keeps a detail pane mounted on the bare route, with no row highlighted', async () => {
    const { el } = await mount();

    expect(el.querySelector('[data-testid="run-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-run-row-ch_1"]')?.classList.contains('selected')).toBe(false);
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

  it('resolves a run-list read failure to the error state', async () => {
    const { fixture, el } = await mount({
      routeOverride: (method, path) => (method === 'GET' && path === '/api/runs' ? stubError(500, {}) : undefined),
    });

    const list = fixture.debugElement.query(By.css('fleet-run-list'));
    expect(list.componentInstance.state()).toBe('error');
    expect(el.textContent).toContain('UNAVAILABLE');
  });
});
