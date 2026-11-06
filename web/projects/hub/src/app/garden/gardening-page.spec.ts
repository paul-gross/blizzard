import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { GardenProposalView } from 'fleet';
import {
  type RequestClientStub,
  gardenCadenceView,
  gardenProjectView,
  gardenProposalClosureView,
  gardenProposalView,
  gardenRoutineView,
  gardenScopeView,
  settle,
  stubHubClient,
} from 'fleet/testing';

import { ProjectLens, provideProjectLens } from '../core/project-lens';
import { GardeningPage } from './gardening-page';

describe('GardeningPage (blizzard#397)', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(proposals: GardenProposalView[]) {
    stub = stubHubClient({
      'GET /api/garden-proposals': () => ({ proposals, next_cursor: null }),
      'GET /api/scopes': () => [gardenScopeView({ slug: 'a' }), gardenScopeView({ slug: 'b' })],
      'GET /api/routines': () => [gardenRoutineView({ routine_id: 'rtn_1', name: 'nightly' })],
      'GET /api/cadences': () => [gardenCadenceView({ slug: 'evening' }), gardenCadenceView({ slug: 'weekly' }), gardenCadenceView({ slug: 'off', retired: true })],
      'GET /api/runs': () => [],
    });
    await TestBed.configureTestingModule({
      imports: [GardeningPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningPage);
    await settle(fixture);
    return fixture;
  }

  it('renders the six surfaces in their order', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    const tabs = Array.from(el.querySelectorAll('[fleetKitTab]')).map((t) => t.getAttribute('data-testid'));
    expect(tabs).toEqual(['gardening-tab-scopes', 'gardening-tab-routines', 'gardening-tab-cadences', 'gardening-tab-runs', 'gardening-tab-findings', 'gardening-tab-proposals']);
    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.textContent).toContain('Runs');
    expect(el.querySelector('[data-testid="gardening-tab-findings"]')?.textContent).toContain('Findings');
  });

  it('draws the proposals count plain when nothing is waiting', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    const count = el.querySelector('[data-testid="gardening-tab-count-proposals"]');
    expect(count?.textContent).toBe('0');
    expect(count?.classList.contains('hot')).toBe(false);
  });

  it('counts complete lists and waiting proposals in amber, without a partial Findings badge', async () => {
    const fixture = await render([
      gardenProposalView({ proposal_id: 'gp_1' }),
      gardenProposalView({ proposal_id: 'gp_2', closure: gardenProposalClosureView({ reason: 'r' }) }),
    ]);
    const el = fixture.nativeElement as HTMLElement;
    const count = (surface: string) => el.querySelector(`[data-testid="gardening-tab-count-${surface}"]`);

    // Only gp_1 (no closure) is still waiting.
    expect(count('proposals')?.textContent).toBe('1');
    expect(count('proposals')?.classList.contains('hot')).toBe(true);
    expect(count('scopes')?.textContent).toBe('2');
    expect(count('routines')?.textContent).toBe('1');
    expect(count('cadences')?.textContent).toBe('3');
    expect(count('runs')?.textContent).toBe('0');
    expect(count('findings')).toBeNull();
    expect(count('scopes')?.classList.contains('hot')).toBe(false);
  });
});

@Component({ selector: 'app-test-garden-tab', template: '' })
class TestGardenTab {}

@Component({ selector: 'app-test-garden-page-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestGardenPageHost {}

describe('GardeningPage under a project lens', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  const PROJECTS = [
    gardenProjectView({ project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' }),
    gardenProjectView({ project_id: 'proj_w', slug: 'winter', name: 'Winter' }),
  ];
  const waiting = (id: string, project_id: string) => gardenProposalView({ proposal_id: id, project_id });
  const TENANT_PROPOSALS = [waiting('gp_1', 'proj_w'), waiting('gp_2', 'proj_w'), waiting('gp_3', 'proj_b')];

  async function render(url = '/p/blizzard/gardening/scopes') {
    stub = stubHubClient({
      'GET /api/projects': () => ({ projects: PROJECTS }),
      'GET /api/garden-proposals': () => ({ proposals: TENANT_PROPOSALS, next_cursor: null }),
      'GET /api/projects/{project}/garden-proposals': () => ({ proposals: [], next_cursor: null }),
    });
    await TestBed.configureTestingModule({
      imports: [TestGardenPageHost],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
          { path: 'gardening', component: TestGardenTab },
          { path: 'gardening/:tab', component: TestGardenTab },
          {
            path: 'p/:projectSlug/gardening',
            component: GardeningPage,
            children: [{ path: ':tab', component: TestGardenTab }],
          },
        ]),
        provideProjectLens(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestGardenPageHost);
    const router = TestBed.inject(Router);
    TestBed.inject(ProjectLens).settle(true);
    await router.navigateByUrl(url);
    await settle(fixture, 8);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('shows the breadcrumb Gardening › {project} › {surface}', async () => {
    const { el } = await render();

    const crumbs = el.querySelector('[data-testid="gardening-crumbs"]')!;
    expect(crumbs.textContent).toContain('Gardening');
    expect(el.querySelector('[data-testid="gardening-crumb-project"]')?.textContent).toContain('Blizzard');
    expect(el.querySelector('[data-testid="gardening-crumb-surface"]')?.textContent).toContain('Scopes');
  });

  it('switches the lens from the project crumb and keeps the surface', async () => {
    const { fixture, router } = await render();

    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project-winter"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/p/winter/gardening/scopes');
  });

  it('lists each project with its proposals waiting, under a header naming the surface a switch keeps', async () => {
    const { fixture } = await render();

    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);

    expect(document.querySelector('[data-testid="gardening-crumb-project-head"]')?.textContent).toContain(
      'Switch project — stays on Scopes',
    );
    expect(document.querySelector('[data-testid="gardening-crumb-waiting-winter"]')?.textContent).toContain(
      '2 proposals waiting',
    );
    expect(document.querySelector('[data-testid="gardening-crumb-waiting-blizzard"]')?.textContent).toContain(
      '1 proposal waiting',
    );
  });

  it('opens the doorway from the Gardening crumb', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLElement>('[data-testid="gardening-crumb-doorway"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening');
  });

  it('activates the Findings tab alone on a findings link', async () => {
    const { el } = await render('/p/blizzard/gardening/findings');

    expect(el.querySelector('[data-testid="gardening-tab-findings"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.classList.contains('active')).toBe(false);
    expect(el.querySelector('[data-testid="gardening-crumb-surface"]')?.textContent).toContain('Findings');
  });

  it('activates the Runs tab alone on a runs link', async () => {
    const { el } = await render('/p/blizzard/gardening/runs');

    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="gardening-tab-findings"]')?.classList.contains('active')).toBe(false);
  });

  it('opens the doorway from the project crumb\'s All projects choice', async () => {
    const { fixture, router } = await render();

    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project-all"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening/scopes');
  });

  it('renders no no-runner warning and never reads the runners', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="gardening-unserved"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-owner"]')).toBeNull();
    expect(stub.requests.map((request) => request.path)).not.toContain('/api/runners');
  });
});
