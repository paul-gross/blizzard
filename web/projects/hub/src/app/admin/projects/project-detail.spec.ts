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

const BLIZZARD: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The platform',
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
};
const SOURCES = {
  sources: [
    { name: 'hub', built_in: true, annotate: false, edit: false },
    { name: 'jira', provider: 'jira', locator: 'acme.atlassian.net', annotate: false, edit: true, revision: 1 },
    { name: 'gh', provider: 'github', locator: 'acme/blizzard', annotate: false, edit: true, revision: 1 },
  ],
};
const REPOSITORIES = {
  repositories: [{ name: 'core', owner: 'acme', repo: 'core', base_branch: 'main', revision: 1 }],
};
const SOURCE_LINKS = {
  source_links: [
    { project_id: 'proj_b', name: 'hub', linked: true, built_in: true },
    { project_id: 'proj_b', name: 'jira', linked: true, revision: 2 },
  ],
};
const HISTORY = {
  changes: [
    {
      id: 7,
      actor: 'ada',
      door: 'board',
      op: 'edit',
      record_key: 'proj_b',
      record_kind: 'project',
      recorded_at: '2026-10-02T00:00:00Z',
      revision: 2,
      diff: [{ field: 'slug', old: 'default', new: 'blizzard' }],
    },
  ],
};

/** An operator who may change configuration. */
const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

function lensOn(project: ProjectView | null) {
  const slug = project?.slug ?? null;
  return {
    slug: signal(slug).asReadonly(),
    lensed: signal(slug !== null).asReadonly(),
    link: (link: InAppLink) => lensLink(link, slug),
  };
}

describe('ProjectDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string, runners: unknown[] = []) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return EDITOR;
      if (path === '/api/projects/proj_b' || path === '/api/projects/default') {
        return method === 'PATCH' ? { ...BLIZZARD, slug: 'blizzard-hub', revision: 4 } : BLIZZARD;
      }
      if (path === '/api/projects/proj_b/source-links') return SOURCE_LINKS;
      if (path === '/api/projects/proj_b/repository-links') return { repository_links: [] };
      if (path === '/api/work-sources') return SOURCES;
      if (path === '/api/repositories') return REPOSITORIES;
      if (path === '/api/runners') return { runners };
      if (path === '/api/config/changes') return HISTORY;
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'admin/projects/:key', component: ProjectDetail },
          { path: 'p/:projectSlug/gardening', children: [] },
        ]),
        { provide: ProjectLens, useValue: lensOn(null) },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement, router: TestBed.inject(Router) };
  }

  const text = (el: HTMLElement, testid: string) => el.querySelector(`[data-testid="${testid}"]`)?.textContent ?? '';

  it('shows the project, the slug it left, its links, and what it lacks', async () => {
    const { el } = await open('/admin/projects/proj_b');
    expect(text(el, 'admin-projects-detail')).toContain('Blizzard');
    expect(text(el, 'admin-projects-former-slugs')).toContain('default');
    expect(text(el, 'admin-projects-sources-rows')).toContain('hub');
    expect(text(el, 'admin-projects-sources-rows')).toContain('jira · acme.atlassian.net');
    expect(el.querySelector('[data-testid="admin-projects-sources-unlink-hub"]')).toBeNull();
    expect(el.querySelector('[data-testid="admin-projects-sources-unlink-jira"]')).not.toBeNull();
    expect(text(el, 'admin-projects-repositories-empty')).toContain('No repository is linked');
    expect(text(el, 'admin-projects-callout')).toContain('no repository and no runner');
    expect(el.querySelector('[data-testid="admin-projects-detail-unserved"]')).not.toBeNull();
  });

  it('names the runners that serve it and drops the runner from the callout', async () => {
    const serving = {
      runner_id: 'rnr_1',
      runner_name: 'desk',
      added_at: '2026-10-01T00:00:00Z',
      connection: 'online',
      hub_paused: false,
      online: true,
      projects: [{ entry: 'blizzard', project_id: 'proj_b' }],
    };
    const { el } = await open('/admin/projects/proj_b', [serving]);
    expect(text(el, 'admin-projects-served-by')).toContain('desk');
    expect(text(el, 'admin-projects-callout')).not.toContain('runner');
    expect(el.querySelector('[data-testid="admin-projects-detail-unserved"]')).toBeNull();
  });

  it('opens a project named by a former slug and rewrites the URL to its id', async () => {
    const { router } = await open('/admin/projects/default');
    expect(router.url).toBe('/admin/projects/proj_b');
  });

  it('changes the slug by id at the shown revision, showing the slug it leaves', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-edit"]')!.click();
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-projects-edit-field-slug"]')!;
    input.value = 'blizzard-hub';
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    const diff = document.querySelector('[data-testid="admin-projects-edit-diff"]')!.textContent!;
    expect(diff).toContain('blizzard');
    expect(diff).toContain('blizzard-hub');
    document.querySelector<HTMLElement>('[data-testid="admin-projects-edit-submit"]')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/projects/proj_b', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ slug: 'blizzard-hub' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('links a source the project does not link yet, and unlinks one at its revision', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-link"]')!.click();
    await settle(harness.fixture);
    const dialog = document.querySelector('[data-testid="admin-projects-link-source-dialog"]')!;
    expect(dialog.textContent).toContain('gh — github · acme/blizzard');
    expect(dialog.textContent).not.toContain('Narrow');
    document.querySelector<HTMLElement>('[data-testid="admin-projects-link-source-submit"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/projects/proj_b/source-links/gh', 'PUT')).toHaveLength(1);

    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-unlink-jira"]')!.click();
    await settle(harness.fixture);
    const retire = stub.forRoute('/api/projects/proj_b/source-links/jira/retire', 'POST');
    expect(retire).toHaveLength(1);
    expect(retire[0].headers['if-match']).toBe('2');
  });
});
