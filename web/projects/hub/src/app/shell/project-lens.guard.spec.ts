import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Location } from '@angular/common';
import { SpyLocation } from '@angular/common/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';

import { PAGE_RELOAD, TAB_TENANT_KEY, TenantContext } from '../core/auth/tenant-context';
import { provideTenantInterceptor } from '../core/auth/tenant.interceptor';
import { LENSED_ROUTE_PATH, ProjectLens } from '../core/project-lens';
import { projectLensGuard } from './project-lens.guard';

@Component({ selector: 'app-test-lensed-view', template: '' })
class TestLensedView {}

const BLIZZARD = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: '',
  revision: 2,
  created_at: '2026-10-01T00:00:00Z',
};

describe('the project lens guard', () => {
  let stub: RequestClientStub;
  let reloads: number;

  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_operator');
    reloads = 0;
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (path === '/api/projects') return { projects: [BLIZZARD] };
      if (path === '/api/projects/old-blizzard') return BLIZZARD;
      if (path === '/api/projects/fresh') return { ...BLIZZARD, project_id: 'proj_f', slug: 'fresh', name: 'Fresh' };
      if (path === '/api/projects/broken') return stubError(500, { detail: 'the store is down' });
      return stubError(404, { detail: 'no such project' });
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          {
            path: LENSED_ROUTE_PATH,
            canActivate: [projectLensGuard],
            children: [{ path: '**', component: TestLensedView }],
          },
          { path: '**', component: TestLensedView },
        ]),
        provideTenantInterceptor(),
        { provide: Location, useClass: SpyLocation },
        { provide: PAGE_RELOAD, useValue: () => reloads++ },
      ],
    });
    TestBed.inject(ProjectLens).settle(true);
  });

  afterEach(() => stub.restore());

  async function open(url: string) {
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    return { router, location: TestBed.inject(Location) as SpyLocation };
  }

  it('opens a current slug as it is, from the project list alone', async () => {
    const { router } = await open('/p/blizzard/events');

    expect(router.url).toBe('/p/blizzard/events');
    expect(stub.forRoute('/api/projects', 'GET').length).toBe(1);
    expect(stub.requests.filter((request) => request.path.startsWith('/api/projects/')).length).toBe(0);
  });

  it('replaces a project’s id in the URL with its current slug', async () => {
    const { router, location } = await open('/p/proj_b/gardening/runs?since=7d');

    expect(router.url).toBe('/p/blizzard/gardening/runs?since=7d');
    expect(location.urlChanges.at(-1)).toBe('replace: /p/blizzard/gardening/runs?since=7d');
  });

  it('replaces a slug the project has left with its current one', async () => {
    const { router, location } = await open('/p/old-blizzard/board');

    expect(router.url).toBe('/p/blizzard/board');
    expect(location.urlChanges.at(-1)).toBe('replace: /p/blizzard/board');
  });

  it('opens a project created after the list was read', async () => {
    const { router } = await open('/p/fresh/board');

    expect(router.url).toBe('/p/fresh/board');
  });

  it('opens an unknown project’s view on All, with a notice, staying in the tenant without a reload', async () => {
    const { router, location } = await open('/p/gone/events');
    await new Promise((resolve) => setTimeout(resolve));

    expect(router.url).toBe('/events');
    expect(location.urlChanges.at(-1)).toBe('replace: /events');
    expect(TestBed.inject(ProjectLens).notice()).toBe('No project “gone” in this tenant. Showing all projects.');
    expect(reloads).toBe(0);
    expect(TestBed.inject(TenantContext).current()).toBe('ten_operator');
    expect(sessionStorage.getItem(TAB_TENANT_KEY)).toBe('ten_operator');
  });

  it('opens All with a notice when the hub cannot answer for the segment', async () => {
    const { router } = await open('/p/broken/board');

    expect(router.url).toBe('/board');
    expect(TestBed.inject(ProjectLens).notice()).toContain('the store is down');
  });

  it('reads no project before the shell has entered its tenant', async () => {
    const lens = TestBed.inject(ProjectLens);
    lens.settle(false);
    // A fresh lens holds no readiness of its own: one the shell has not settled waits.
    const router = TestBed.inject(Router);
    const pending = router.navigateByUrl('/p/blizzard/board');
    await new Promise((resolve) => setTimeout(resolve));

    expect(stub.forRoute('/api/projects', 'GET').length).toBe(0);

    lens.settle(true);
    await pending;
    expect(router.url).toBe('/p/blizzard/board');
  });
});
