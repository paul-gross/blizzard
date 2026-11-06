import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  hostRunnerView,
  pauseStateView,
  type RequestClientStub,
  runnerDashboardView,
  runnerStatusView,
  servedProjectStatusView,
  settle,
  stubError,
  stubRunnerClient,
} from 'fleet/testing';

import { PanelFiltersBar } from './panel-filters-bar';

/** Two serving runners — one braked here — and one halted. */
const RUNNERS = {
  items: [
    hostRunnerView({ runner_id: 'rn_1', name: 'r-claude', bindings: [{ project: 'blizzard', workspace_id: 'ws', agents: 4 }], max_agents: 4 }),
    hostRunnerView({
      runner_id: 'rn_2',
      name: 'r-opencode',
      pause: pauseStateView({ local: true, effective: true, local_reason: 'usage limit' }),
      bindings: [{ project: 'winter', workspace_id: 'ws', agents: 2 }],
      max_agents: 2,
    }),
    hostRunnerView({ runner_id: null, name: 'r-broken', state: 'halted', served: false, pause: null, halted_reasons: ['no hub token'] }),
  ],
};

/** A runner's dashboard naming the one project it serves. */
function dashboard(slug: string, name: string) {
  return runnerDashboardView({
    runner: runnerStatusView({
      served_projects: [servedProjectStatusView({ entry: slug, project_id: `proj_${slug}`, slug, name, color: 'slate' })],
    }),
  });
}

const DASHBOARDS: Record<string, ReturnType<typeof dashboard>> = {
  rn_1: dashboard('blizzard', 'Blizzard'),
  rn_2: dashboard('winter', 'Winter'),
};

describe('PanelFiltersBar', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRunnerClient({
      'GET /api/host/runners': () => RUNNERS,
      'GET /runners/{runner_id}/api/dashboard': (_request, { runner_id }) =>
        DASHBOARDS[runner_id] ?? stubError(404, { detail: 'unknown runner' }),
    });
  });

  afterEach(() => stub.restore());

  async function render(url: string) {
    await TestBed.configureTestingModule({
      imports: [PanelFiltersBar],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: '**', children: [] }]),
      ],
    }).compileComponents();
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    const fixture = TestBed.createComponent(PanelFiltersBar);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const chip = (testid: string) => el.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`);
    return { fixture, router, el, chip };
  }

  it('offers every runner on the host, a halted one disabled and a braked one marked', async () => {
    const { el, chip } = await render('/board');

    expect(
      Array.from(el.querySelectorAll('[data-testid="runner-filter"] button')).map((b) => b.getAttribute('data-testid')),
    ).toEqual(['runner-filter-all', 'runner-filter-r-claude', 'runner-filter-r-opencode', 'runner-filter-r-broken']);
    expect(chip('runner-filter-r-broken')?.disabled).toBe(true);
    expect(chip('runner-filter-r-opencode')?.querySelector('[data-testid="runner-brake"]')?.getAttribute('data-glyph')).toBe(
      'pause',
    );
  });

  it('offers every project the host’s runners serve, by name', async () => {
    const { el } = await render('/board');

    expect(
      Array.from(el.querySelectorAll('[data-testid="project-filter"] button')).map((b) => b.textContent?.trim()),
    ).toEqual(['All', 'Blizzard', 'Winter']);
  });

  it('selects the filters the URL names, All while absent', async () => {
    const { chip } = await render('/board?runner=r-claude');

    expect(chip('runner-filter-r-claude')?.getAttribute('aria-pressed')).toBe('true');
    expect(chip('project-filter-all')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('rewrites only the Runner param on a runner choice, keeping the page and every other param', async () => {
    const { fixture, router, chip } = await render('/events?project=winter&chunk=ch_1');

    chip('runner-filter-r-claude')?.click();
    await settle(fixture);

    expect(router.url).toBe('/events?project=winter&chunk=ch_1&runner=r-claude');
    expect(chip('runner-filter-r-claude')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('rewrites only the Project param on a project choice, and drops it for All', async () => {
    const { fixture, router, chip } = await render('/board?runner=r-claude&chunk=ch_1');

    chip('project-filter-blizzard')?.click();
    await settle(fixture);
    expect(router.url).toBe('/board?runner=r-claude&chunk=ch_1&project=blizzard');

    chip('project-filter-all')?.click();
    await settle(fixture);
    expect(router.url).toBe('/board?runner=r-claude&chunk=ch_1');
  });

  it('sits as two compact rows in its mobile variant', async () => {
    const { fixture, el } = await render('/board');
    fixture.componentRef.setInput('variant', 'mobile');
    await settle(fixture);

    expect(el.classList.contains('mobile')).toBe(true);
    expect(el.querySelectorAll('app-filter-chips.compact')).toHaveLength(2);
  });
});
