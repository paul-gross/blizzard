import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { InAppLink } from 'fleet/shell';
import {
  configChangesPage,
  credentialSummary,
  memberView,
  OPERATOR_ME_RESPONSE,
  projectView,
  type RequestClientStub,
  repositoryLinkView,
  repositorySummary,
  secretView,
  settle,
  sourceLinkView,
  stubHubClient,
  workSourceSummary,
} from 'fleet/testing';

import { ProjectLens, lensLink } from '../core/project-lens';
import { AdminShell } from './admin-shell';

const PROJECTS = {
  projects: [
    projectView({ project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', description: '', revision: 1, created_at: '' }),
    projectView({ project_id: 'proj_w', slug: 'winter', name: 'Winter', description: '', revision: 1, created_at: '' }),
  ],
};
const SOURCES = {
  sources: [
    workSourceSummary({ name: 'hub', slug: 'hub', work_source_id: null, built_in: true }),
    workSourceSummary({ name: 'jira', slug: 'jira', work_source_id: 'wsrc_j' }),
    workSourceSummary({ name: 'gh', slug: 'gh', work_source_id: 'wsrc_g' }),
    workSourceSummary({ name: 'Unlinked', slug: 'other', work_source_id: 'wsrc_other' }),
  ],
};
const REPOSITORIES = {
  repositories: [
    repositorySummary({ name: 'core', slug: 'core', repository_id: 'repo_c' }),
    repositorySummary({ name: 'web', slug: 'web', repository_id: 'repo_w' }),
    repositorySummary({ name: 'Unlinked', slug: 'other', repository_id: 'repo_other' }),
  ],
};
const SECRETS = [secretView({ name: 'gh-token' })];

function lensOn(slug: string | null) {
  return {
    slug: signal(slug).asReadonly(),
    lensed: signal(slug !== null).asReadonly(),
    link: (link: InAppLink) => lensLink(link, slug),
  };
}

describe('AdminShell', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function open(slug: string | null, permissions: typeof OPERATOR_ME_RESPONSE.permissions) {
    stub = stubHubClient({
      'GET /api/me': () => {
        const tenants = OPERATOR_ME_RESPONSE.tenants.map((tenant) => ({ ...tenant, permissions }));
        return { ...OPERATOR_ME_RESPONSE, permissions, tenants };
      },
      'GET /api/members': () => [memberView({ user_id: 'u1' }), memberView({ user_id: 'u2' }), memberView({ user_id: 'u3' })],
      'GET /api/projects': () => PROJECTS,
      'GET /api/work-sources': () => SOURCES,
      'GET /api/repositories': () => REPOSITORIES,
      'GET /api/secrets': () => SECRETS,
      'GET /api/credentials': () => ({ credentials: [credentialSummary(), credentialSummary({ credential_id: 'cred_b' })], bindings: [], forge_bindings: [] }),
      'GET /api/config/changes': () => configChangesPage({ total: slug === null ? 42 : 7 }),
      'GET /api/projects/{project}/source-links': () => ({
        source_links: [
          sourceLinkView({ project_id: 'proj_w', name: 'hub', slug: 'hub', built_in: true, revision: null }),
          sourceLinkView({ project_id: 'proj_w', name: 'wsrc_j', slug: 'jira' }),
          sourceLinkView({ project_id: 'proj_w', name: 'wsrc_g', slug: 'gh' }),
          sourceLinkView({ project_id: 'proj_w', name: 'wsrc_other', slug: 'other', linked: false }),
          sourceLinkView({ project_id: 'proj_w', name: 'wsrc_retired', slug: 'retired' }),
        ],
      }),
      'GET /api/projects/{project}/repository-links': () => ({
        repository_links: [
          repositoryLinkView({ project_id: 'proj_w', name: 'core', repository_id: 'repo_c' }),
          repositoryLinkView({ project_id: 'proj_w', name: 'web', repository_id: 'repo_w' }),
          repositoryLinkView({ project_id: 'proj_w', name: 'Unlinked', repository_id: 'repo_other', linked: false }),
          repositoryLinkView({ project_id: 'proj_w', name: 'Retired', repository_id: 'repo_retired' }),
        ],
      }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin', component: AdminShell }]),
        { provide: ProjectLens, useValue: lensOn(slug) },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin');
    await settle(harness.fixture);
    return harness.routeNativeElement as HTMLElement;
  }

  const count = (el: HTMLElement, tab: string) =>
    el.querySelector(`[data-testid="admin-tab-${tab}"] .cnt`)?.textContent?.trim() ?? null;

  it('counts what each tab holds, Changes by the log\'s total', async () => {
    const el = await open(null, [...OPERATOR_ME_RESPONSE.permissions, 'user:manage']);
    expect(count(el, 'members')).toBe('3');
    expect(count(el, 'projects')).toBe('2');
    expect(count(el, 'work-sources')).toBe('4');
    expect(count(el, 'repositories')).toBe('3');
    expect(count(el, 'credentials')).toBe('2');
    expect(count(el, 'secrets')).toBe('1');
    expect(count(el, 'changes')).toBe('42');
  });

  it("counts what the lensed project links, and no members for an actor who may not read them", async () => {
    const el = await open(
      'winter',
      OPERATOR_ME_RESPONSE.permissions.filter((permission) => permission !== 'user:manage'),
    );
    expect(count(el, 'work-sources')).toBe('3');
    expect(count(el, 'repositories')).toBe('2');
    expect(new URLSearchParams(stub.forRoute('/api/work-sources')[0].search).get('include_retired')).toBe('false');
    expect(new URLSearchParams(stub.forRoute('/api/repositories')[0].search).get('include_retired')).toBe('false');
    expect(count(el, 'projects')).toBe('2');
    expect(count(el, 'members')).toBeNull();
    expect(stub.forRoute('/api/members')).toHaveLength(0);
    expect(count(el, 'changes')).toBe('7');
  });
});
