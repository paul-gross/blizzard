import { CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { MeResponse, ProjectView } from 'fleet';
import {
  chunkCountsView,
  configChangesPage,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  repositoryLinkView,
  repositorySummary,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { ProjectLens } from '../../core/project-lens';
import { ProjectDetail } from './project-detail';

const PROJECT: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: '',
  color: 'teal',
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
};
const repository = (id: string, slug: string) =>
  repositorySummary({
    repository_id: id,
    slug,
    name: slug,
    owner: 'acme',
    repo: slug,
    base_branch: 'main',
    revision: 1,
  });
const link = (id: string, slug: string, position: number | null) =>
  repositoryLinkView({
    project_id: 'proj_b',
    repository_id: id,
    slug,
    name: slug,
    linked: true,
    position,
    revision: 1,
  });
const LINKS = {
  repository_links: [link('repo_a', 'alpha', 1), link('repo_b', 'bravo', 2), link('repo_x', 'xray', null)],
};
type PositionAnswer = NonNullable<Parameters<typeof stubHubClient>[0]['POST /api/projects/{project}/landing-order/position']>;
const EDITOR: MeResponse = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

/** Render what has arrived without waiting for the app to be stable, for a write held open on purpose. */
async function pump(fixture: { detectChanges: () => void }): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

describe('ProjectDetail landing order', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function open(position: PositionAnswer) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/projects/{project}': () => PROJECT,
      'GET /api/projects/{project}/source-links': () => ({ source_links: [] }),
      'GET /api/projects/{project}/repository-links': () => LINKS,
      'GET /api/repositories': () => ({
        repositories: [repository('repo_a', 'alpha'), repository('repo_b', 'bravo'), repository('repo_x', 'xray')],
      }),
      'GET /api/work-sources': () => ({ sources: [] }),
      'POST /api/projects/{project}/landing-order/position': position,
      'GET /api/runners': () => ({ runners: [] }),
      'GET /api/config/changes': () => configChangesPage(),
      'GET /api/chunk-counts': () => chunkCountsView(),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/projects/:key', component: ProjectDetail }]),
        { provide: ProjectLens, useValue: { slug: signal(null), lensed: signal(false), link: (l: unknown) => l } },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/projects/proj_b');
    await settle(harness.fixture);
    const el = harness.routeNativeElement as HTMLElement;
    el.querySelector<HTMLElement>('[data-testid="admin-projects-tab-repositories"]')!.click();
    await settle(harness.fixture);
    return { harness, el };
  }

  const names = (el: HTMLElement, section: string) =>
    [...el.querySelectorAll(`[data-testid="admin-projects-repositories-${section}"] .name`)].map((node) => node.textContent?.trim());

  function dropToFront(harness: RouterTestingHarness): void {
    const lists = harness.fixture.debugElement.queryAll(By.directive(CdkDropList)).map((list) => list.injector.get(CdkDropList));
    const ranked = lists.find((list) => list.data === 'ranked')!;
    const unranked = lists.find((list) => list.data === 'unranked')!;
    ranked.dropped.emit({
      item: { data: 'repo_x' },
      previousContainer: unranked,
      container: ranked,
      previousIndex: 0,
      currentIndex: 0,
    } as unknown as CdkDragDrop<unknown>);
  }

  it('renders a pending move in its new place before it settles, and writes one position', async () => {
    let release: () => void = () => undefined;
    const { harness, el } = await open(() => new Promise<typeof LINKS>((resolve) => (release = () => resolve(LINKS))));
    expect(names(el, 'ranked')).toEqual(['alpha', 'bravo']);

    dropToFront(harness);
    await pump(harness.fixture);

    expect(names(el, 'ranked')).toEqual(['xray', 'alpha', 'bravo']);
    expect(names(el, 'unranked')).toEqual([]);
    const posts = stub.forRoute('/api/projects/proj_b/landing-order/position', 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ repository: 'repo_x', after_repository: null });
    release();
    await pump(harness.fixture);
  });

  it("shows the hub's refusal on the panel's error line", async () => {
    const { harness, el } = await open(() => stubError(409, { detail: 'not linked' }));

    dropToFront(harness);
    await settle(harness.fixture);

    expect(el.querySelector('[data-testid="admin-projects-repositories-error"]')?.textContent).toContain('not linked');
  });
});
