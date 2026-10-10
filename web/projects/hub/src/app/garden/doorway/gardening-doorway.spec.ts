import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type GardenProposalView, type ProjectView, type RoutineView, type RunRowView, type RunnerRegistryView, type ScopeView, hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningDoorway } from './gardening-doorway';
import { doorwayCards, waitingLabel } from './gardening-doorway.model';

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
  { chunk_id: 'ch_1', project_id: 'proj_b' },
  { chunk_id: 'ch_2', project_id: 'proj_b' },
] as RunRowView[];

const DOORWAY_PROPOSALS = [
  { proposal_id: 'gp_1', project_id: 'proj_b', closure: null },
  { proposal_id: 'gp_2', project_id: 'proj_b', closure: { kind: 'accepted' } },
  { proposal_id: 'gp_3', project_id: 'proj_w', closure: null },
] as unknown as GardenProposalView[];

const DOORWAY_RUNNERS = [
  { runner_id: 'run_1', runner_name: 'r1', online: true, retired: false, projects: [{ entry: 'blizzard', project_id: 'proj_b' }] },
] as unknown as RunnerRegistryView[];

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
    case '/api/runners':
      return { runners: DOORWAY_RUNNERS };
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
  runners: DOORWAY_RUNNERS,
};

describe('doorwayCards', () => {
  it('gives one card per project, by name, counting only the records its project holds', () => {
    const [blizzard, winter] = doorwayCards(READS);

    expect(blizzard).toEqual(
      expect.objectContaining({
        slug: 'blizzard',
        routines: ['garden-weekly'],
        scopes: 2,
        runs: 2,
        waiting: 1,
        unplanted: false,
        unserved: false,
      }),
    );
    expect(winter).toEqual(
      expect.objectContaining({ slug: 'winter', routines: [], scopes: 0, runs: 0, waiting: 1, unplanted: true }),
    );
  });

  it('opens each project\'s garden on its first tab, under its lens', () => {
    expect(doorwayCards(READS).map((card) => card.link)).toEqual([
      ['/p', 'blizzard', 'gardening', 'runs'],
      ['/p', 'winter', 'gardening', 'runs'],
    ]);
  });

  it('marks a project no live runner serves, and says nothing until the runners are read', () => {
    expect(doorwayCards(READS).map((card) => card.unserved)).toEqual([false, true]);
    expect(doorwayCards({ ...READS, runners: undefined }).map((card) => card.unserved)).toEqual([null, null]);
    const offline = DOORWAY_RUNNERS.map((runner) => ({ ...runner, online: false }));
    expect(doorwayCards({ ...READS, runners: offline }).map((card) => card.unserved)).toEqual([true, true]);
  });

  it('counts a waiting proposal in the singular and the plural', () => {
    expect(waitingLabel(1)).toBe('1 proposal waiting');
    expect(waitingLabel(3)).toBe('3 proposals waiting');
  });
});

describe('GardeningDoorway', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  async function render() {
    stub = stubRequestClient(hubClient, doorwayRoute);
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

  it('reads the tenant-wide lists, never a project\'s nested ones', async () => {
    await render();

    const paths = stub.requests.map((request) => request.path);
    for (const read of ['/api/projects', '/api/scopes', '/api/routines', '/api/runs', '/api/garden-proposals']) {
      expect(paths).toContain(read);
    }
    expect(paths.filter((path) => path.startsWith('/api/projects/'))).toEqual([]);
  });

  it('renders a card per project with its standing and the way in', async () => {
    const el = await render();

    const blizzard = el.querySelector('[data-testid="gardening-doorway-card-blizzard"]')!;
    expect(blizzard.textContent).toContain('garden-weekly');
    expect(blizzard.querySelector('[data-testid="gardening-doorway-waiting"]')?.textContent).toContain(
      '1 proposal waiting',
    );
    expect(blizzard.querySelector('[data-testid="gardening-doorway-unserved"]')).toBeNull();
    const open = blizzard.querySelector<HTMLAnchorElement>('[data-testid="gardening-doorway-open-blizzard"]')!;
    expect(open.getAttribute('href')).toBe('/p/blizzard/gardening/runs');

    const winter = el.querySelector('[data-testid="gardening-doorway-card-winter"]')!;
    expect(winter.textContent).toContain('Nothing planted yet');
    expect(winter.querySelector('[data-testid="gardening-doorway-unserved"]')?.textContent).toContain(
      'No runner serves this project',
    );
  });
});
