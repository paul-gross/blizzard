import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type ProjectView } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ProjectLens, lensLink } from '../../core/project-lens';
import { ProjectDetail } from './project-detail';

const BLIZZARD: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The platform',
  color: 'teal',
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
};
const SOURCES = {
  sources: [
    { name: 'hub', slug: 'hub', work_source_id: null, built_in: true, annotate: false, edit: false },
    {
      work_source_id: 'wsrc_j',
      slug: 'jira',
      name: 'jira',
      provider: 'jira',
      locator: 'acme.atlassian.net',
      annotate: false,
      edit: true,
      revision: 1,
    },
    {
      work_source_id: 'wsrc_g',
      slug: 'gh',
      name: 'gh',
      provider: 'github',
      locator: 'acme/blizzard',
      annotate: false,
      edit: true,
      revision: 1,
    },
  ],
};
const REPOSITORIES = {
  repositories: [
    {
      repository_id: 'repo_c',
      slug: 'core',
      name: 'Core',
      owner: 'acme',
      repo: 'core',
      base_branch: 'main',
      revision: 1,
    },
  ],
};
const SOURCE_LINKS = {
  source_links: [
    { project_id: 'proj_b', name: 'hub', slug: 'hub', linked: true, built_in: true },
    { project_id: 'proj_b', name: 'wsrc_j', slug: 'jira', linked: true, revision: 2 },
  ],
};
const HISTORY = {
  changes: [
    {
      id: 7,
      actor: 'ada',
      actor_name: 'Ada Lovelace',
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

/** An operator who may change configuration and read the members. */
const EDITOR = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit', 'user:manage'],
};

function lensOn(project: ProjectView | null) {
  const slug = project?.slug ?? null;
  return {
    slug: signal(slug).asReadonly(),
    lensed: signal(slug !== null).asReadonly(),
    link: (link: InAppLink) => lensLink(link, slug),
  };
}

/** Render what has arrived without waiting for the app to be stable, for a read held open on purpose. */
async function pump(fixture: { detectChanges: () => void }): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

describe('ProjectDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(
    url: string,
    runners: unknown[] = [],
    patched?: () => unknown,
    override?: (method: string, path: string) => unknown,
    holding = false,
  ) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const overridden = override?.(method, path);
      if (overridden !== undefined) return overridden;
      if (path === '/api/me') return EDITOR;
      if (path === '/api/projects/proj_b' || path === '/api/projects/default') {
        if (method === 'PATCH' && patched) return patched();
        return method === 'PATCH' ? { ...BLIZZARD, slug: 'blizzard-hub', revision: 4 } : BLIZZARD;
      }
      if (path === '/api/projects/proj_b/source-links') return SOURCE_LINKS;
      if (path === '/api/projects/proj_b/repository-links') return { repository_links: [] };
      if (path === '/api/work-sources') return SOURCES;
      if (path === '/api/repositories') return REPOSITORIES;
      if (path === '/api/runners') return { runners };
      if (path === '/api/config/changes') return HISTORY;
      if (path === '/api/chunk-counts') return { ready: 2, running: 1, done: 40, done_since: 4 };
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
    if (holding) {
      // A read held open keeps the app unstable, so stability can never be awaited.
      void harness.navigateByUrl(url);
      await pump(harness.fixture);
    } else {
      await harness.navigateByUrl(url);
      await settle(harness.fixture);
    }
    return { harness, el: harness.routeNativeElement as HTMLElement, router: TestBed.inject(Router) };
  }

  const text = (el: HTMLElement, testid: string) => el.querySelector(`[data-testid="${testid}"]`)?.textContent ?? '';

  async function click(harness: RouterTestingHarness, el: HTMLElement, testid: string) {
    el.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!.click();
    await settle(harness.fixture);
  }

  it('overviews the project: its facts, where work comes from and lands, and what it lacks', async () => {
    const { el } = await open('/admin/projects/proj_b');
    expect(text(el, 'admin-projects-detail')).toContain('Blizzard');
    expect(text(el, 'admin-projects-description')).toContain('The platform');
    expect(text(el, 'admin-projects-former-slugs')).toContain('default');
    expect(text(el, 'admin-projects-queue')).toContain('2 ready · 1 running · 4 done today');
    expect(text(el, 'admin-projects-draws')).toContain('jira');
    expect(text(el, 'admin-projects-draws')).toContain('jira · acme.atlassian.net');
    expect(text(el, 'admin-projects-lands')).toContain('No repository is linked');
    expect(text(el, 'admin-projects-served-by')).toContain('No runner serves this project.');
    expect(text(el, 'admin-projects-callout')).toContain('no repository and no runner');
    expect(text(el, 'admin-projects-callout')).toContain('Its 2 ready chunks wait in the queue');
    expect(text(el, 'admin-projects-tab-sources')).toContain('2');
    expect(text(el, 'admin-projects-tab-repositories')).toContain('0');
  });

  it("names who made each revision by the hub's name for them", async () => {
    const { el } = await open('/admin/projects/proj_b');
    expect(text(el, 'admin-projects-last-change')).toContain('by Ada Lovelace via board');
    expect(text(el, 'admin-projects-revisions')).toContain('Ada Lovelace via board');
    expect(text(el, 'admin-projects-revisions')).not.toContain('ada via');
    expect(stub.forRoute('/api/members')).toHaveLength(0);
  });

  it("asks for today's done from the browser's local midnight", async () => {
    await open('/admin/projects/proj_b');
    const now = new Date();
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
    const reads = stub.forRoute('/api/chunk-counts');
    const search = new URLSearchParams(reads.at(-1)?.search ?? '');
    expect(search.get('project')).toBe('blizzard');
    expect(search.get('done_since')).toBe(midnight);
  });

  it('opens the Linked sources tab from its overview card, where the links are edited', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    expect(el.querySelector('[data-testid="admin-projects-sources-rows"]')).toBeNull();
    await click(harness, el, 'admin-projects-draws-open');
    expect(text(el, 'admin-projects-sources-rows')).toContain('hub');
    expect(text(el, 'admin-projects-sources-rows')).toContain('jira · acme.atlassian.net');
    expect(el.querySelector('[data-testid="admin-projects-sources-unlink-hub"]')).toBeNull();
    expect(el.querySelector('[data-testid="admin-projects-sources-unlink-jira"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="admin-projects-served-by-open"]')).toBeNull();
    await click(harness, el, 'admin-projects-tab-repositories');
    expect(text(el, 'admin-projects-repositories-empty')).toContain('No repository is linked');
  });

  it('names the runners that serve it and drops the runner from the callout', async () => {
    const serving = {
      runner_id: 'rnr_1',
      runner_name: 'desk',
      added_at: '2026-10-01T00:00:00Z',
      connection: 'online',
      hub_paused: false,
      online: true,
      projects: [{ entry: 'blizzard', project_id: 'proj_b', claimable: true }],
    };
    const { el } = await open('/admin/projects/proj_b', [serving]);
    expect(text(el, 'admin-projects-served-by')).toContain('desk');
    expect(text(el, 'admin-projects-served-by')).toContain('online');
    expect(text(el, 'admin-projects-callout')).not.toContain('runner');
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

  it('states the color and saves a picked one by id at the shown revision', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    expect(text(el, 'admin-projects-color')).toContain('teal');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-color-open"]')!.click();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="project-color-pink"]')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/projects/proj_b', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ color: 'pink' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('saves a committed custom hex as the color', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-color-open"]')!.click();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="project-color-custom"]')!.click();
    await settle(harness.fixture);
    const field = document.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')!;
    field.value = '#FF8800';
    field.dispatchEvent(new Event('input'));
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/projects/proj_b', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ color: '#ff8800' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it("shows the hub's refusal of a color in the dialog", async () => {
    const refusal = stubError(422, {
      detail: [{ loc: ['body', 'color'], msg: 'must be one of red, or a #rrggbb hex color', type: 'value_error' }],
    });
    const { harness, el } = await open('/admin/projects/proj_b', [], () => refusal);
    el.querySelector<HTMLElement>('[data-testid="admin-projects-color-open"]')!.click();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="project-color-pink"]')!.click();
    await settle(harness.fixture);
    expect(document.querySelector('[data-testid="admin-projects-color-error"]')?.textContent).toContain('#rrggbb');
  });

  it('links a source the project does not link yet, and unlinks one at its revision', async () => {
    const { harness, el } = await open('/admin/projects/proj_b');
    await click(harness, el, 'admin-projects-tab-sources');
    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-link"]')!.click();
    await settle(harness.fixture);
    const dialog = document.querySelector('[data-testid="admin-projects-link-source-dialog"]')!;
    expect(dialog.textContent).toContain('gh — github · acme/blizzard');
    expect(dialog.textContent).not.toContain('Narrow');
    document.querySelector<HTMLElement>('[data-testid="admin-projects-link-source-submit"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/projects/proj_b/source-links/wsrc_g', 'PUT')).toHaveLength(1);

    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-unlink-jira"]')!.click();
    await settle(harness.fixture);
    const retire = stub.forRoute('/api/projects/proj_b/source-links/wsrc_j/retire', 'POST');
    expect(retire).toHaveLength(1);
    expect(retire[0].headers['if-match']).toBe('2');
  });

  it('shows loading, not the empty copy, while the link and runner reads are pending', async () => {
    const never = new Promise<never>(() => undefined);
    const { el } = await open('/admin/projects/proj_b', [], undefined, (_method, path) =>
      path === '/api/projects/proj_b/source-links' ||
      path === '/api/projects/proj_b/repository-links' ||
      path === '/api/runners'
        ? never
        : undefined,
      true,
    );
    for (const card of ['admin-projects-draws', 'admin-projects-lands', 'admin-projects-served-by']) {
      expect(text(el, card)).toContain('Loading…');
    }
    expect(el.textContent).not.toContain('No work source is linked.');
    expect(el.textContent).not.toContain('No runner serves this project.');
    expect(el.textContent).not.toContain('No repository is linked');
  });

  it('keeps every row with an unlink in flight disabled, and shows the first failure after a second unlink starts', async () => {
    const retires = new Map<string, { resolve: () => void; reject: () => void }>();
    const links = {
      source_links: [
        ...SOURCE_LINKS.source_links,
        { project_id: 'proj_b', name: 'wsrc_g', slug: 'gh', linked: true, revision: 5 },
      ],
    };
    const { harness, el } = await open('/admin/projects/proj_b', [], undefined, (method, path) => {
      if (path === '/api/projects/proj_b/source-links') return links;
      const retire = /source-links\/(\w+)\/retire$/.exec(path);
      if (method === 'POST' && retire) {
        return new Promise((resolve) => {
          retires.set(retire[1], {
            resolve: () => resolve({}),
            reject: () => resolve(stubError(409, { detail: 'stale revision' })),
          });
        });
      }
      return undefined;
    });
    await click(harness, el, 'admin-projects-tab-sources');
    const unlink = (name: string) => el.querySelector<HTMLButtonElement>(`[data-testid="admin-projects-sources-unlink-${name}"]`)!;

    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-unlink-jira"]')!.click();
    await pump(harness.fixture);
    expect(unlink('jira').disabled).toBe(true);
    expect(unlink('gh').disabled).toBe(false);
    el.querySelector<HTMLElement>('[data-testid="admin-projects-sources-unlink-gh"]')!.click();
    await pump(harness.fixture);
    expect(unlink('jira').disabled).toBe(true);
    expect(unlink('gh').disabled).toBe(true);

    retires.get('wsrc_j')!.reject();
    await pump(harness.fixture);
    expect(text(el, 'admin-projects-sources-error')).toContain('stale revision');
    expect(unlink('gh').disabled).toBe(true);
  });
});
