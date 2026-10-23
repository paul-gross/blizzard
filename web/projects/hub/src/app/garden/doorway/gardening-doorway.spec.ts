import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type GardenProposalView, type ProjectView, type RoutineView, type RunRowView, type ScopeView, hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { GardeningDoorway } from './gardening-doorway';
import { doorwayCards, openGardenLabel, sweepingLabel, waitingLabel } from './gardening-doorway.model';

/** The doorway's tenant-wide reads, for a tenant of two projects — `blizzard` planted and served, `winter` neither. */
const DOORWAY_PROJECTS = [
  { project_id: 'proj_w', slug: 'winter', name: 'Winter' },
  { project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' },
] as ProjectView[];

const DOORWAY_SCOPES = [
  { slug: 'hub-api', project_id: 'proj_b', retired: false },
  { slug: 'web', project_id: 'proj_b', retired: false },
  { slug: 'old', project_id: 'proj_b', retired: true },
] as ScopeView[];

const DOORWAY_ROUTINES = [
  { name: 'garden-weekly', routine_id: 'rt_1', project_id: 'proj_b', retired: false },
  { name: 'retired-one', routine_id: 'rt_2', project_id: 'proj_b', retired: true },
] as RoutineView[];

const DOORWAY_RUNS = [
  {
    chunk_id: 'ch_1',
    project_id: 'proj_b',
    outcome: 'running',
    minted_at: '2026-03-09T00:00:00Z',
    delivered: [],
  },
  {
    chunk_id: 'ch_2',
    project_id: 'proj_b',
    outcome: 'done',
    minted_at: '2026-03-05T00:00:00Z',
    delivered: [{ finding_set_id: 'fs_1', added_count: 3, gone_count: 0 }],
  },
  {
    chunk_id: 'ch_3',
    project_id: 'proj_b',
    outcome: 'done',
    minted_at: '2026-03-01T00:00:00Z',
    delivered: [
      { finding_set_id: 'fs_1', added_count: 1, gone_count: 2 },
      { finding_set_id: 'fs_2', added_count: 1, gone_count: 0 },
    ],
  },
] as unknown as RunRowView[];

const DOORWAY_PROPOSALS = [
  { proposal_id: 'gp_1', project_id: 'proj_b', closure: null },
  { proposal_id: 'gp_2', project_id: 'proj_b', closure: { kind: 'accepted' } },
  { proposal_id: 'gp_3', project_id: 'proj_w', closure: null },
] as unknown as GardenProposalView[];

/** A stub table answering the doorway's reads on All. */
function doorwayRoute(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  switch (path) {
    case '/api/projects':
      return { projects: DOORWAY_PROJECTS };
    case '/api/scopes':
      return DOORWAY_SCOPES;
    case '/api/routines':
      return DOORWAY_ROUTINES;
    case '/api/runs':
      return DOORWAY_RUNS;
    case '/api/garden-proposals':
      return { proposals: DOORWAY_PROPOSALS, next_cursor: null };
    case '/api/me':
      return { tenants: [{ tenant_id: 't_1', name: 'grosscode', role: 'owner', permissions: [] }] };
    default:
      return {};
  }
}

const READS = {
  projects: DOORWAY_PROJECTS,
  scopes: DOORWAY_SCOPES,
  routines: DOORWAY_ROUTINES,
  runs: DOORWAY_RUNS,
  proposals: DOORWAY_PROPOSALS,
};

describe('doorwayCards', () => {
  it('gives one card per project, by name, counting only the records its project holds', () => {
    const [blizzard, winter] = doorwayCards(READS);

    expect(blizzard).toEqual(
      expect.objectContaining({
        slug: 'blizzard',
        routines: ['garden-weekly'],
        scopes: 2,
        inProgress: 1,
        findings: 5,
        lastRun: '2026-03-05T00:00:00Z',
        waiting: 1,
        unplanted: false,
      }),
    );
    expect(winter).toEqual(
      expect.objectContaining({
        slug: 'winter',
        routines: [],
        scopes: 0,
        inProgress: 0,
        findings: 0,
        lastRun: null,
        waiting: 1,
        unplanted: true,
      }),
    );
  });

  it('opens each project\'s garden on its landing tab, under its lens', () => {
    expect(doorwayCards(READS).map((card) => card.link)).toEqual([
      ['/p', 'blizzard', 'gardening', 'proposals'],
      ['/p', 'winter', 'gardening', 'proposals'],
    ]);
  });

  it('counts a waiting proposal in the singular and the plural', () => {
    expect(waitingLabel(1)).toBe('1 proposal waiting');
    expect(waitingLabel(3)).toBe('3 proposals waiting');
  });

  it('reads a project idle, or how many of its runs are sweeping', () => {
    expect(sweepingLabel(0)).toBe('idle');
    expect(sweepingLabel(1)).toBe('1 run in progress');
    expect(sweepingLabel(2)).toBe('2 runs in progress');
  });

  it('writes the way in with a possessive that follows the name', () => {
    expect(openGardenLabel('Default')).toBe("Open Default's garden");
    expect(openGardenLabel('Widgets')).toBe("Open Widgets' garden");
  });
});

describe('GardeningDoorway', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  async function render(route: (method: string, path: string) => unknown = doorwayRoute) {
    stub = stubRequestClient(hubClient, route);
    TestBed.configureTestingModule({
      imports: [GardeningDoorway],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
    const fixture = TestBed.createComponent(GardeningDoorway);
    await settle(fixture, 8);
    return fixture.nativeElement as HTMLElement;
  }

  it('says where it is and whose gardens these are', async () => {
    const el = await render();

    const crumbs = el.querySelector('[data-testid="gardening-doorway-crumbs"]')?.textContent ?? '';
    expect(crumbs).toContain('Gardening');
    expect(crumbs).toContain('All projects');
    expect(crumbs).not.toContain('pick one to go in');
    expect(el.textContent).toContain('Gardens');
    expect(el.querySelector('[data-testid="gardening-doorway-hint"]')).toBeNull();
  });

  it('reads the tenant-wide lists, never a project\'s nested ones', async () => {
    await render();

    const paths = stub.requests.map((request) => request.path);
    for (const read of ['/api/projects', '/api/scopes', '/api/routines', '/api/runs', '/api/garden-proposals']) {
      expect(paths).toContain(read);
    }
    expect(paths.filter((path) => path.startsWith('/api/projects/'))).toEqual([]);
    expect(paths).not.toContain('/api/runners');
  });

  it('hands the grouped cards to the presentational list', async () => {
    const el = await render();

    const cards = [...el.querySelectorAll('app-gardening-doorway-cards [data-testid^="gardening-doorway-card-"]')];
    expect(cards.map((card) => card.getAttribute('data-testid'))).toEqual([
      'gardening-doorway-card-blizzard',
      'gardening-doorway-card-winter',
    ]);
  });

  it('says it is loading until the reads answer', async () => {
    const never = new Promise<never>(() => undefined);
    const el = await render((method, path) => (path === '/api/projects' ? never : doorwayRoute(method, path)));

    expect(el.querySelector('[data-testid="gardening-doorway-loading"]')).not.toBeNull();
    expect(el.querySelector('app-gardening-doorway-cards')).toBeNull();
  });

  it('says so when a read fails', async () => {
    const el = await render((method, path) => (path === '/api/scopes' ? stubError(500, { detail: 'the store is down' }) : doorwayRoute(method, path)));

    expect(el.querySelector('[data-testid="gardening-doorway-error"]')).not.toBeNull();
    expect(el.querySelector('app-gardening-doorway-cards')).toBeNull();
  });

  it('says so when the tenant has no projects', async () => {
    const el = await render((method, path) => (path === '/api/projects' ? { projects: [] } : doorwayRoute(method, path)));

    expect(el.querySelector('[data-testid="gardening-doorway-empty"]')?.textContent).toContain('NO PROJECTS IN THIS TENANT');
  });
});
