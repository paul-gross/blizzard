import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, type Routes, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type ProjectView, hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ProjectLens, provideProjectLens } from '../core/project-lens';
import { gardenPathOf } from './garden-lens.guard';
import { GARDENING_ROUTES } from './gardening.routes';

@Component({
  selector: 'app-test-garden-lens-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestGardenLensHost {}

/** Stands in for a project's garden where a case asserts only where a link lands, not what the tab then reads. */
@Component({ selector: 'app-test-lensed-garden', template: '' })
class TestLensedGarden {}

const LANDING_ONLY: Routes = [{ path: '**', component: TestLensedGarden }];

function project(slug: string, name: string): ProjectView {
  return { project_id: `proj_${slug}`, slug, name } as ProjectView;
}

const SOLO = [project('solo', 'Solo')];
const SEVERAL = [project('blizzard', 'Blizzard'), project('winter', 'Winter')];

/**
 * The gardening guards on All (`garden-lens.guard.ts`), through the real router mounted as the app mounts it — at
 * the root and under `p/:projectSlug`: one project skips the doorway, a record's link opens in its own project, and a
 * name is never matched across several projects.
 */
describe('the gardening guards on All', () => {
  let stub: RequestClientStub;

  async function boot(
    projects: readonly ProjectView[],
    records: Record<string, unknown> = {},
    lensedGarden: Routes = GARDENING_ROUTES,
  ) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method !== 'GET') return {};
      if (path === '/api/projects') return { projects };
      if (path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (path in records) return records[path];
      if (path.endsWith('/garden-proposals')) return { proposals: [], next_cursor: null };
      if (path.endsWith('/findings')) return { findings: [], next_cursor: null };
      if (path === '/api/runners') return { runners: [] };
      return [];
    });
    TestBed.configureTestingModule({
      imports: [TestGardenLensHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'gardening', children: GARDENING_ROUTES },
          { path: 'p/:projectSlug', children: [{ path: 'gardening', children: lensedGarden }] },
        ]),
        provideProjectLens(),
      ],
    });
    const fixture = TestBed.createComponent(TestGardenLensHost);
    const lens = TestBed.inject(ProjectLens);
    lens.settle(true);
    return { fixture, lens, router: TestBed.inject(Router) };
  }

  afterEach(() => stub?.restore());

  it('opens the only project\'s garden instead of the doorway', async () => {
    const { fixture, router } = await boot(SOLO);

    await router.navigateByUrl('/gardening');
    await settle(fixture, 8);

    expect(router.url).toBe('/p/solo/gardening/proposals');
  });

  it('opens any gardening link under the only project\'s lens, a scope by its slug included', async () => {
    const { fixture, router } = await boot(SOLO);

    await router.navigateByUrl('/gardening/scopes/hub-api?x=1');
    await settle(fixture, 8);

    expect(router.url).toBe('/p/solo/gardening/scopes/hub-api?x=1');
  });

  it('opens the doorway on All when the tenant holds several projects', async () => {
    const { fixture, router } = await boot(SEVERAL);

    await router.navigateByUrl('/gardening');
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-doorway-card-blizzard"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-doorway-card-winter"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-subnav"]')).toBeNull();
  });

  it('opens a bare tab on All at the doorway, with no notice', async () => {
    const { fixture, router, lens } = await boot(SEVERAL);

    await router.navigateByUrl('/gardening/findings');
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening');
    expect(lens.notice()).toBeNull();
  });

  it('opens a run\'s un-lensed link under the lens of the project its own read names', async () => {
    const { fixture, router } = await boot(
      SEVERAL,
      { '/api/runs/ch_1': { chunk_id: 'ch_1', project_slug: 'winter' } },
      LANDING_ONLY,
    );

    await router.navigateByUrl('/gardening/runs/ch_1');
    await settle(fixture, 8);

    expect(router.url).toBe('/p/winter/gardening/runs/ch_1');
  });

  it('follows a finding and a proposal to their projects the same way', async () => {
    const { fixture, router } = await boot(
      SEVERAL,
      {
        '/api/findings/fnd_1': { finding_id: 'fnd_1', project_slug: 'blizzard' },
        '/api/garden-proposals/gprop_1': { proposal_id: 'gprop_1', project_slug: 'winter' },
      },
      LANDING_ONLY,
    );

    await router.navigateByUrl('/gardening/findings/fnd_1');
    await settle(fixture, 8);
    expect(router.url).toBe('/p/blizzard/gardening/findings/fnd_1');

    await router.navigateByUrl('/gardening/proposals/gprop_1');
    await settle(fixture, 8);
    expect(router.url).toBe('/p/winter/gardening/proposals/gprop_1');
  });

  it('opens the doorway with a notice for a record the tenant does not hold', async () => {
    const { fixture, router, lens } = await boot(SEVERAL, {
      '/api/runs/ch_gone': stubError(404, { detail: 'no such run' }),
    });

    await router.navigateByUrl('/gardening/runs/ch_gone');
    await settle(fixture, 8);

    expect(router.url).toBe('/gardening');
    expect(lens.notice()).toContain('ch_gone');
  });

  for (const [tab, noun] of [
    ['scopes', 'scope'],
    ['routines', 'routine'],
  ] as const) {
    it(`never matches a ${noun}'s name across projects: the doorway opens with a notice`, async () => {
      const { fixture, router, lens } = await boot(SEVERAL);

      await router.navigateByUrl(`/gardening/${tab}/nightly`);
      await settle(fixture, 8);

      expect(router.url).toBe('/gardening');
      expect(lens.notice()).toContain(`${noun} “nightly”`);
      const lookups = stub.requests.filter((request) => request.path.includes('/nightly'));
      expect(lookups).toEqual([]);
    });
  }

  it('opens a lensed bare gardening path on the project\'s landing tab, reading nothing to decide', async () => {
    const { fixture, router } = await boot(SEVERAL);

    await router.navigateByUrl('/p/winter/gardening');
    await settle(fixture, 8);

    expect(router.url).toBe('/p/winter/gardening/proposals');
  });
});

describe('gardenPathOf', () => {
  it('reads the tab and the record of a gardening URL, lensed or not', () => {
    expect(gardenPathOf('/gardening')).toEqual({ tab: null, record: null });
    expect(gardenPathOf('/gardening/runs?since=x')).toEqual({ tab: 'runs', record: null });
    expect(gardenPathOf('/p/blizzard/gardening/scopes/hub%20api#top')).toEqual({ tab: 'scopes', record: 'hub api' });
  });
});
