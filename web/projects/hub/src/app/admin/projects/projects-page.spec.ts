import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { MeResponse, ProjectView } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import {
  chunkCountsView,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  repositoryLinkView,
  runnerRegistryView,
  settle,
  sourceLinkView,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { ProjectLens, lensLink } from '../../core/project-lens';
import { ProjectDetail } from './project-detail';
import { ProjectsPage } from './projects-page';

const BLIZZARD: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The platform',
  color: 'teal',
  revision: 2,
  created_at: '2026-10-01T00:00:00Z',
};
const WINTER: ProjectView = { ...BLIZZARD, project_id: 'proj_w', slug: 'winter', name: 'Winter', description: '' };
const NOVA: ProjectView = { ...WINTER, project_id: 'proj_n', slug: 'nova', name: 'Nova' };
const SERVING = runnerRegistryView({
  runner_id: 'rnr_1',
  runner_name: 'desk',
  added_at: '2026-10-01T00:00:00Z',
  connection: 'online',
  hub_paused: false,
  online: true,
  projects: [{ entry: 'blizzard', project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', claimable: true, color: null }],
});

/** An operator who may change configuration. */
const EDITOR: MeResponse = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

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
    const projects = new Map(
      [WINTER, BLIZZARD, NOVA].flatMap((project) => [
        [project.project_id, project],
        [project.slug, project],
      ]),
    );
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'POST /api/projects': () => NOVA,
      'GET /api/projects': () => ({ projects: [WINTER, BLIZZARD] }),
      'GET /api/projects/{project}': (_request, { project }) =>
        projects.get(project) ?? stubError(404, { detail: `unknown project ${project}` }),
      'GET /api/runners': () => ({ runners: [SERVING] }),
      'GET /api/projects/{project}/source-links': (_request, { project }) => ({
        source_links:
          project === 'proj_b'
            ? [sourceLinkView({ project_id: 'proj_b', name: 'jira', slug: 'jira', linked: true, revision: 1 })]
            : [],
      }),
      'GET /api/projects/{project}/repository-links': (_request, { project }) => ({
        repository_links:
          project === 'proj_b'
            ? [repositoryLinkView({ project_id: 'proj_b', name: 'core', slug: 'core', linked: true, revision: 1 })]
            : [],
      }),
      'GET /api/chunk-counts': () => chunkCountsView({ ready: 2, running: 1, done: 4 }),
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

  it('lists every project on All with its light and short id, marking the wired one and what the other needs, and opens the first', async () => {
    const { el, router } = await open('/admin/projects');
    expect(el.querySelector('[data-testid="projects-table-row-proj_w"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="projects-table-light-proj_b"]')!.getAttribute('aria-label')).toBe('wired');
    expect(el.querySelector('[data-testid="projects-table-ref-proj_b"]')!.textContent).toContain('PROJ-');
    expect(el.querySelector('[data-testid="projects-table-needs-proj_w"]')!.textContent).toContain(
      'needs a work source, a repository, a runner',
    );
    expect(router.url).toBe('/admin/projects/proj_b');
    expect(stub.forRoute('/api/chunk-counts', 'GET').map((call) => call.search)).not.toContain('?project=winter');
  });

  it('heads the list and puts New project beside it', async () => {
    const { el } = await open('/admin/projects');
    const head = el.querySelector('[data-testid="admin-projects-list-panel"] .p-hdr')!;
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
