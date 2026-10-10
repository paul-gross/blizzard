import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ProjectLens, provideProjectLens } from '../core/project-lens';
import { GardeningPage } from './gardening-page';

describe('GardeningPage (blizzard#397)', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(proposals: readonly unknown[]) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return { proposals, next_cursor: null };
      return {};
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

  it('renders the five deep-linkable sub-tabs, in order', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-tab-scopes"]')?.textContent).toContain('Scopes');
    expect(el.querySelector('[data-testid="gardening-tab-routines"]')?.textContent).toContain('Routines');
    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.textContent).toContain('Runs');
    expect(el.querySelector('[data-testid="gardening-tab-findings"]')?.textContent).toContain('Findings');
    expect(el.querySelector('[data-testid="gardening-tab-proposals"]')?.textContent).toContain('Proposals');
    const tabs = Array.from(el.querySelectorAll('[fleetKitTab]')).map((t) => t.getAttribute('data-testid'));
    expect(tabs).toEqual([
      'gardening-tab-runs',
      'gardening-tab-findings',
      'gardening-tab-proposals',
      'gardening-tab-routines',
      'gardening-tab-scopes',
    ]);
  });

  it('omits the proposals count when nothing is waiting', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposals-count"]')).toBeNull();
  });

  it('carries the waiting count on Proposals only, and no count on the other four tabs', async () => {
    const fixture = await render([
      { proposal_id: 'gp_1', routine_name: 'comments', class: 'x', body: 'b', created_at: '2026-01-01T00:00:00Z', findings: [] },
      {
        proposal_id: 'gp_2',
        routine_name: 'comments',
        class: 'x',
        body: 'b',
        created_at: '2026-01-01T00:00:00Z',
        findings: [],
        closure: { closure: 'passed', reason: 'r', closed_by: 'u', closed_at: '2026-01-02T00:00:00Z', item_outcome: null, source: null, ref: null },
      },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    // Only gp_1 (no closure) is still waiting.
    expect(el.querySelector('[data-testid="gardening-proposals-count"]')?.textContent).toBe('1');
    expect(el.querySelector('[data-testid="gardening-tab-scopes"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-routines"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-runs"] .badge')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-tab-findings"] .badge')).toBeNull();
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
    { project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' },
    { project_id: 'proj_w', slug: 'winter', name: 'Winter' },
  ];

  async function render(runners: readonly unknown[], url = '/p/blizzard/gardening/scopes') {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method !== 'GET') return {};
      if (path === '/api/projects') return { projects: PROJECTS };
      if (path === '/api/runners') return { runners };
      if (path.endsWith('/garden-proposals')) return { proposals: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestGardenPageHost],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
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
    const { el } = await render([]);

    const crumbs = el.querySelector('[data-testid="gardening-crumbs"]')!;
    expect(crumbs.textContent).toContain('Gardening');
    expect(el.querySelector('[data-testid="gardening-crumb-project"]')?.textContent).toContain('Blizzard');
    expect(el.querySelector('[data-testid="gardening-crumb-surface"]')?.textContent).toContain('Scopes');
  });

  it('switches the lens from the project crumb and keeps the surface', async () => {
    const { fixture, router } = await render([]);

    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project-winter"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/p/winter/gardening/scopes');
  });

  it('opens the doorway from the project crumb\'s All projects choice', async () => {
    const { fixture, router } = await render([]);

    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project"]')!.click();
    await settle(fixture, 4);
    document.querySelector<HTMLElement>('[data-testid="gardening-crumb-project-all"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening/scopes');
  });

  it('says so when no live runner serves the project', async () => {
    const { el } = await render([
      { runner_id: 'r1', runner_name: 'r1', online: true, retired: false, projects: [{ entry: 'winter', project_id: 'proj_w' }] },
    ]);

    expect(el.querySelector('[data-testid="gardening-unserved"]')?.textContent).toContain('No runner serves Blizzard');
  });

  it('says nothing when a live runner serves the project', async () => {
    const { el } = await render([
      { runner_id: 'r1', runner_name: 'r1', online: true, retired: false, projects: [{ entry: 'blizzard', project_id: 'proj_b' }] },
    ]);

    expect(el.querySelector('[data-testid="gardening-unserved"]')).toBeNull();
  });
});
