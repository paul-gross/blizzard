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
      if (path === '/api/projects/proj_b/source-links') {
        return { source_links: [{ project_id: 'proj_b', name: 'jira', linked: true, revision: 1 }] };
      }
      if (path === '/api/projects/proj_b/repository-links') {
        return { repository_links: [{ project_id: 'proj_b', name: 'core', linked: true, revision: 1 }] };
      }
      if (path.endsWith('-links')) return {};
      if (path === '/api/chunk-counts') return { ready: 2, running: 1, done: 4 };
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

  it('lists every project on All with its figures, marking the wired one and what the other needs', async () => {
    const { el } = await open('/admin/projects');
    expect(el.querySelector('[data-testid="projects-table-row-proj_w"]')).not.toBeNull();
    const figures = el.querySelector('[data-testid="projects-table-figures-proj_b"]')!.textContent!;
    expect(figures).toContain('1 linked source');
    expect(figures).toContain('1 linked repo');
    expect(figures).toContain('3 in queue');
    expect(el.querySelector('[data-testid="projects-table-wired-proj_b"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="projects-table-needs-proj_w"]')!.textContent).toContain(
      'needs a work source, a repository, a runner',
    );
    expect(el.querySelector('[data-testid="admin-projects-detail-empty"]')).not.toBeNull();
    expect(stub.forRoute('/api/chunk-counts', 'GET').map((call) => call.search)).toEqual(
      expect.arrayContaining(['?project=blizzard', '?project=winter']),
    );
  });

  it('heads the list with the tenant and puts New project beside it', async () => {
    const { el } = await open('/admin/projects');
    const head = el.querySelector('.s-head')!;
    expect(head.textContent).toContain('Projects');
    expect(head.querySelector('[data-testid="admin-projects-new"]')).not.toBeNull();
  });

  it('opens a picked project by its id', async () => {
    const { harness, el, router } = await open('/admin/projects');
    el.querySelector<HTMLElement>('[data-testid="projects-table-row-proj_w"]')!.click();
    await settle(harness.fixture);
    expect(router.url).toBe('/admin/projects/proj_w');
  });

  it('creates a project from its slug and description, naming it by the slug, then opens it', async () => {
    const { harness, el, router } = await open('/admin/projects');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-new"]')!.click();
    await settle(harness.fixture);
    const dialog = document.querySelector('[data-testid="admin-projects-create-dialog"]')!;
    expect(dialog.querySelector('[data-testid="admin-projects-create-hint"]')!.textContent).toContain('former slug');
    for (const [key, value] of [
      ['slug', 'nova'],
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
      name: 'nova',
      description: 'A new body of work',
    });
    expect(router.url).toBe('/admin/projects/proj_n');
  });
});
