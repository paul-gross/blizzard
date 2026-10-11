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
      if (method === 'GET' && path === '/api/scopes') return [{ slug: 'a' }, { slug: 'b' }];
      if (method === 'GET' && path === '/api/routines') return [{ name: 'nightly' }];
      if (method === 'GET' && path === '/api/runs') return [];
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

  it('renders the four surfaces in their order, runs and findings sharing one', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    const tabs = Array.from(el.querySelectorAll('[fleetKitTab]')).map((t) => t.getAttribute('data-testid'));
    expect(tabs).toEqual(['gardening-tab-scopes', 'gardening-tab-routines', 'gardening-tab-runs', 'gardening-tab-proposals']);
    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.textContent).toContain('Runs & findings');
  });

  it('draws the proposals count plain when nothing is waiting', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    const count = el.querySelector('[data-testid="gardening-tab-count-proposals"]');
    expect(count?.textContent).toBe('0');
    expect(count?.classList.contains('hot')).toBe(false);
  });

  it('counts each list on its tab, Proposals counting the waiting ones in amber', async () => {
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
    const count = (surface: string) => el.querySelector(`[data-testid="gardening-tab-count-${surface}"]`);

    // Only gp_1 (no closure) is still waiting.
    expect(count('proposals')?.textContent).toBe('1');
    expect(count('proposals')?.classList.contains('hot')).toBe(true);
    expect(count('scopes')?.textContent).toBe('2');
    expect(count('routines')?.textContent).toBe('1');
    expect(count('runs')?.textContent).toBe('0');
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
    { project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' },
    { project_id: 'proj_w', slug: 'winter', name: 'Winter' },
  ];
  const waiting = (id: string, project_id: string) => ({
    proposal_id: id,
    project_id,
    routine_name: 'comments',
    class: 'x',
    body: 'b',
    created_at: '2026-01-01T00:00:00Z',
    findings: [],
  });
  const TENANT_PROPOSALS = [waiting('gp_1', 'proj_w'), waiting('gp_2', 'proj_w'), waiting('gp_3', 'proj_b')];

  async function render(runners: readonly unknown[], url = '/p/blizzard/gardening/scopes') {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method !== 'GET') return {};
      if (path === '/api/projects') return { projects: PROJECTS };
      if (path === '/api/runners') return { runners };
      if (path === '/api/garden-proposals') return { proposals: TENANT_PROPOSALS, next_cursor: null };
      if (path.endsWith('/garden-proposals')) return { proposals: [], next_cursor: null };
      return {};
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

  it('lists each project with its proposals waiting, under a header naming the surface a switch keeps', async () => {
    const { fixture } = await render([]);

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
    const { fixture, router, el } = await render([]);

    el.querySelector<HTMLElement>('[data-testid="gardening-crumb-doorway"]')!.click();
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening');
  });

  it('says whose records the strip holds', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="gardening-owner"]')?.textContent).toContain('every record here belongs to Blizzard');
  });

  it('keeps a findings link on the Runs & findings tab, with a switch between its two lists', async () => {
    const { el } = await render([], '/p/blizzard/gardening/findings');

    expect(el.querySelector('[data-testid="gardening-tab-runs"]')?.classList.contains('active')).toBe(true);
    expect(el.querySelector('[data-testid="gardening-crumb-surface"]')?.textContent).toContain('Runs & findings');
    expect(el.querySelector('[data-testid="gardening-switch-findings"]')?.classList.contains('on')).toBe(true);
    expect(el.querySelector('[data-testid="gardening-switch-runs"]')?.classList.contains('on')).toBe(false);
  });

  it('shows no runs and findings switch on another surface', async () => {
    const { el } = await render([]);

    expect(el.querySelector('[data-testid="gardening-runs-switch"]')).toBeNull();
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
