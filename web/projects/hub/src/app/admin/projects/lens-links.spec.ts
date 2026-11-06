import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { ProjectView } from 'fleet';
import { HUB_PROJECT_FILTER, type InAppLink } from 'fleet/shell';
import {
  configChangesPage,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  repositorySummary,
  settle,
  sourceLinkView,
  stubHubClient,
  workSourceSummary,
} from 'fleet/testing';

import { ProjectLens, lensLink } from '../../core/project-lens';
import { ChangesPage } from '../changes/changes-page';
import { RepositoriesPage } from '../repositories/repositories-page';
import { WorkSourcesPage } from '../work-sources/work-sources-page';

const WINTER: ProjectView = {
  project_id: 'proj_w',
  slug: 'winter',
  name: 'Winter',
  description: '',
  color: 'teal',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
};

/**
 * The tenant lists a project lens narrows: under a lens Work sources and Repositories show
 * the records the project links, and Changes asks the hub for the project's changes.
 */
describe('admin lists under the project lens', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function open(url: string, lens: ProjectView | null) {
    const slug = lens?.slug ?? null;
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/work-sources': () => ({
        sources: [
          workSourceSummary({ name: 'hub', slug: 'hub', work_source_id: null, provider: null, built_in: true, annotate: false, edit: false, revision: null }),
          workSourceSummary({ name: 'jira', slug: 'jira', work_source_id: null, provider: 'jira', annotate: false, edit: true, revision: 1 }),
          workSourceSummary({ name: 'gh', slug: 'gh', work_source_id: null, provider: 'github', annotate: false, edit: true, revision: 1 }),
        ],
      }),
      'GET /api/repositories': () => ({
        repositories: [
          repositorySummary({ repository_id: 'repo_c', name: 'core', slug: 'core', owner: 'a', repo: 'core', base_branch: 'main', revision: 1 }),
          repositorySummary({ repository_id: 'repo_a', name: 'archive', slug: 'archive', owner: 'a', repo: 'archive', base_branch: 'main', revision: 1 }),
        ],
      }),
      'GET /api/projects': () => ({ projects: [WINTER] }),
      'GET /api/projects/{project}/source-links': () => ({
        source_links: [
          sourceLinkView({ project_id: 'proj_w', name: 'hub', slug: 'hub', linked: true, built_in: true, revision: null }),
          sourceLinkView({ project_id: 'proj_w', name: 'jira', slug: 'jira', linked: true, revision: 1 }),
          sourceLinkView({ project_id: 'proj_w', name: 'gh', slug: 'gh', linked: false, revision: 2 }),
        ],
      }),
      'GET /api/projects/{project}/repository-links': () => ({ repository_links: [] }),
      'GET /api/config/changes': () => configChangesPage(),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'work-sources', component: WorkSourcesPage, children: [{ path: '', children: [] }] },
          { path: 'repositories', component: RepositoriesPage, children: [{ path: '', children: [] }] },
          { path: 'changes', component: ChangesPage, children: [{ path: '', children: [] }] },
        ]),
        {
          provide: ProjectLens,
          useValue: {
            slug: signal(slug).asReadonly(),
            lensed: signal(slug !== null).asReadonly(),
            link: (link: InAppLink) => lensLink(link, slug),
          },
        },
        { provide: HUB_PROJECT_FILTER, useValue: signal(slug).asReadonly() },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return harness.routeNativeElement as HTMLElement;
  }

  const rowKeys = (el: HTMLElement, prefix: string) =>
    [...el.querySelectorAll(`[data-testid^="${prefix}-row-"]`)].map((row) =>
      row.getAttribute('data-testid')!.slice(`${prefix}-row-`.length),
    );

  it('lists every work source on All', async () => {
    const el = await open('/work-sources', null);
    expect(rowKeys(el, 'admin-work-sources').sort()).toEqual(['gh', 'hub', 'jira']);
  });

  it('narrows Work sources to the links in force under a lens', async () => {
    const el = await open('/work-sources', WINTER);
    expect(rowKeys(el, 'admin-work-sources').sort()).toEqual(['hub', 'jira']);
    expect(el.textContent).toContain('linked by Winter');
  });

  it('says a lensed project links no repository rather than that the tenant has none', async () => {
    const el = await open('/repositories', WINTER);
    expect(rowKeys(el, 'admin-repositories')).toEqual([]);
    expect(el.textContent).toContain('Winter links no repositories.');
  });

  it("asks for the lensed project's changes, and every change on All", async () => {
    await open('/changes', WINTER);
    expect(stub.forRoute('/api/config/changes', 'GET')[0].search).toContain('project=winter');
    stub.restore();
    TestBed.resetTestingModule();
    await open('/changes', null);
    expect(stub.forRoute('/api/config/changes', 'GET')[0].search).not.toContain('project=');
  });
});
