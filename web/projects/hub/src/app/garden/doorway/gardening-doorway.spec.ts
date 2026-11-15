import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  gardenProjectView, gardenProposalClosureView, gardenProposalsPageView, gardenProposalView,
  gardenRoutineView, type RequestClientStub, settle, stubError, stubHubClient,
} from 'fleet/testing';

import { GardeningDoorway } from './gardening-doorway';
import { doorwayCards } from './gardening-doorway.model';

const projects = [
  gardenProjectView({ project_id: 'w', slug: 'winter', name: 'Winter' }),
  gardenProjectView({ project_id: 'b', slug: 'blizzard', name: 'Blizzard' }),
];
const routines = [gardenRoutineView({ project_id: 'b', routine_id: 'rt_1', name: 'weekly' })];
const proposals = [
  gardenProposalView({ project_id: 'b', proposal_id: 'gp_1' }),
  gardenProposalView({ project_id: 'b', proposal_id: 'gp_2', closure: gardenProposalClosureView({ closure: 'accepted' }) }),
];
type Routes = Parameters<typeof stubHubClient>[0];

function routes(): Routes {
  return {
    'GET /api/projects': () => ({ projects }),
    'GET /api/cadences': () => [],
    'GET /api/routines': () => routines,
    'GET /api/garden-proposals': () => gardenProposalsPageView({ proposals }),
  };
}

describe('doorwayCards', () => {
  it('sorts projects and counts only pending proposals in their own project', () => {
    const cards = doorwayCards(projects, [], routines, proposals);
    expect(cards.map(({ slug, counts, href }) => ({ slug, counts, href }))).toEqual([
      { slug: 'blizzard', counts: '1 routine · 1 pending proposal', href: '/p/blizzard/gardening/proposals' },
      { slug: 'winter', counts: '0 pending proposals', href: '/p/winter/gardening/proposals' },
    ]);
  });
});

describe('GardeningDoorway', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  async function render(overrides: Routes = {}) {
    stub = stubHubClient({ ...routes(), ...overrides });
    TestBed.configureTestingModule({
      imports: [GardeningDoorway],
      providers: [provideZonelessChangeDetection(), provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(GardeningDoorway);
    await settle(fixture, 8);
    return fixture.nativeElement as HTMLElement;
  }

  it('reads the tenant-wide inputs and renders plain project cards without breadcrumbs', async () => {
    const el = await render();
    for (const path of ['/api/projects', '/api/cadences', '/api/routines', '/api/garden-proposals']) {
      expect(stub.requests.map((request) => request.path)).toContain(path);
    }
    expect(el.querySelector('[data-testid="gardening-doorway-crumbs"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-doorway-card-blizzard"]')?.textContent).toContain('1 pending proposal');
    expect(el.querySelector('[data-testid="gardening-doorway-card-winter"]')?.textContent).toContain('0 pending proposals');
  });

  it('renders placeholders while a read is pending', async () => {
    const el = await render({ 'GET /api/projects': () => new Promise<never>(() => undefined) });
    expect(el.querySelector('[data-testid="gardening-doorway-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="gardening-doorway-card-blizzard"]')).toBeNull();
  });

  it('shows an error instead of partial counts', async () => {
    const el = await render({ 'GET /api/cadences': () => stubError(500, { detail: 'down' }) });
    expect(el.querySelector('[data-testid="gardening-doorway-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="gardening-doorway-card-blizzard"]')).toBeNull();
  });

  it('shows the empty state for a tenant without projects', async () => {
    const el = await render({ 'GET /api/projects': () => ({ projects: [] }) });
    expect(el.querySelector('[data-testid="gardening-doorway-empty"]')?.textContent).toContain('NO PROJECTS IN THIS TENANT');
  });
});
