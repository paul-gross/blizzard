import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type ProjectView } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ProjectLens, lensLink } from '../../core/project-lens';
import { ProjectDetail } from './project-detail';
import { ProjectsPage } from './projects-page';

const BLIZZARD: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The platform',
  revision: 2,
  created_at: '2026-10-01T00:00:00Z',
};
const WINTER: ProjectView = { ...BLIZZARD, project_id: 'proj_w', slug: 'winter', name: 'Winter', description: '' };
const NOVA: ProjectView = { ...WINTER, project_id: 'proj_n', slug: 'nova', name: 'Nova' };
const SERVING = {
  runner_id: 'rnr_1',
  runner_name: 'desk',
  added_at: '2026-10-01T00:00:00Z',
  connection: 'online',
  hub_paused: false,
  online: true,
  projects: [{ entry: 'blizzard', project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' }],
};

/** An operator who may change configuration. */
const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

/** A lens fixed on `project` (or All), linking the way the shell's lens does. */
function lensOn(project: ProjectView | null) {
  const slug = project?.slug ?? null;
  return {
    slug: signal(slug).asReadonly(),
    lensed: signal(slug !== null).asReadonly(),
    link: (link: InAppLink) => lensLink(link, slug),
  };
}

const PAGE_ROUTE = {
  component: ProjectsPage,
  children: [
    { path: '', component: ProjectDetail },
    { path: ':key', component: ProjectDetail },
  ],
};

describe('ProjectsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string, lens: ProjectView | null = null) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return EDITOR;
      if (path === '/api/projects' && method === 'POST') return NOVA;
      if (path === '/api/projects/proj_n') return NOVA;
      if (path === '/api/projects') return { projects: [WINTER, BLIZZARD] };
      if (path === '/api/runners') return { runners: [SERVING] };
      if (path === '/api/projects/proj_b') return BLIZZARD;
      if (path === '/api/projects/proj_w') return WINTER;
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'admin/projects', ...PAGE_ROUTE },
          { path: 'p/:projectSlug/admin/projects', ...PAGE_ROUTE },
        ]),
        { provide: ProjectLens, useValue: lensOn(lens) },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement, router: TestBed.inject(Router) };
  }

  it('lists every project on All, badging the one no live runner serves', async () => {
    const { el } = await open('/admin/projects');
    expect(el.querySelector('[data-testid="projects-table-row-proj_b"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="projects-table-row-proj_w"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="projects-table-unserved-proj_b"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-unserved-proj_w"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="admin-projects-detail-empty"]')).not.toBeNull();
  });

  it('opens a picked project by its id', async () => {
    const { harness, el, router } = await open('/admin/projects');
    el.querySelector<HTMLElement>('[data-testid="projects-table-row-proj_w"]')!.click();
    await settle(harness.fixture);
    expect(router.url).toBe('/admin/projects/proj_w');
  });

  it('under a lens lists only the lensed project and opens it', async () => {
    const { el, router } = await open('/p/winter/admin/projects', WINTER);
    expect(router.url).toBe('/p/winter/admin/projects/proj_w');
    expect(el.querySelector('[data-testid="projects-table-row-proj_b"]')).toBeNull();
    expect(el.querySelector('[data-testid="projects-table-row-proj_w"]')).not.toBeNull();
  });

  it('creates a project from slug, name and description, then opens it', async () => {
    const { harness, el, router } = await open('/admin/projects');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-new"]')!.click();
    await settle(harness.fixture);
    const dialog = document.querySelector('[data-testid="admin-projects-create-dialog"]')!;
    expect(dialog.querySelector('[data-testid="admin-projects-create-hint"]')!.textContent).toContain('former slug');
    for (const [key, value] of [
      ['slug', 'nova'],
      ['name', 'Nova'],
      ['description', 'A new body of work'],
    ]) {
      const input = dialog.querySelector<HTMLInputElement>(`[data-testid="admin-projects-create-field-${key}"]`)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
    }
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="admin-projects-create-submit"]')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/projects', 'POST')[0].body).toEqual({
      slug: 'nova',
      name: 'Nova',
      description: 'A new body of work',
    });
    expect(router.url).toBe('/admin/projects/proj_n');
  });
});
