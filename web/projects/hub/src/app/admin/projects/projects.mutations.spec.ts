import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubConfigKey, hubProjectLinksKey } from 'fleet';
import {
  projectView,
  type RequestClientStub,
  repositoryLinkView,
  sourceLinkView,
  stubHubClient,
} from 'fleet/testing';
import {
  injectCreateProjectMutation,
  injectEditProjectMutation,
  injectLandingPositionMutation,
  injectProjectLinkMutation,
} from './projects.mutations';

describe('project mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'POST /api/projects': () => projectView(),
      'PATCH /api/projects/{project}': () => projectView({ slug: 'blizzard-hub', revision: 5 }),
      'PUT /api/projects/{project}/source-links/{source}': () => sourceLinkView(),
      'POST /api/projects/{project}/source-links/{source}/retire': () => sourceLinkView({ linked: false, revision: 3 }),
      'PUT /api/projects/{project}/repository-links/{repository}': () => repositoryLinkView(),
      'POST /api/projects/{project}/repository-links/{repository}/retire': () =>
        repositoryLinkView({ linked: false, revision: 6 }),
      'POST /api/projects/{project}/landing-order/position': () => ({ repository_links: [] }),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): unknown[] {
    return spy.mock.calls.map((call) => (call[0] as { queryKey: unknown }).queryKey);
  }

  it('creates, answers the new project, then invalidates every config read', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateProjectMutation());

    const created = await mutation.mutateAsync({ slug: 'blizzard', name: 'Blizzard', description: '' });

    expect(stub.forRoute('/api/projects', 'POST')[0].body).toEqual({ slug: 'blizzard', name: 'Blizzard', description: '' });
    expect(created.project_id).toBe('proj_b');
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });

  it('edits by id with the shown revision as If-Match, so a slug change never moves the address', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectEditProjectMutation());

    await mutation.mutateAsync({ projectId: 'proj_b', revision: 4, body: { slug: 'blizzard-hub' } });

    const calls = stub.forRoute('/api/projects/proj_b', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ slug: 'blizzard-hub' });
    expect(calls[0].headers['if-match']).toBe('4');
  });

  it('links with PUT and unlinks by retiring the link at its revision', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectProjectLinkMutation());

    await mutation.mutateAsync({ projectId: 'proj_b', kind: 'source', name: 'jira', linked: true, revision: null });
    await mutation.mutateAsync({ projectId: 'proj_b', kind: 'source', name: 'jira', linked: false, revision: 2 });
    await mutation.mutateAsync({ projectId: 'proj_b', kind: 'repository', name: 'core', linked: true, revision: null });
    await mutation.mutateAsync({ projectId: 'proj_b', kind: 'repository', name: 'core', linked: false, revision: 5 });

    expect(stub.forRoute('/api/projects/proj_b/source-links/jira', 'PUT')).toHaveLength(1);
    expect(stub.forRoute('/api/projects/proj_b/source-links/jira/retire', 'POST')[0].headers['if-match']).toBe('2');
    expect(stub.forRoute('/api/projects/proj_b/repository-links/core', 'PUT')).toHaveLength(1);
    expect(stub.forRoute('/api/projects/proj_b/repository-links/core/retire', 'POST')[0].headers['if-match']).toBe('5');
    expect(invalidatedKeys(spy)).toContainEqual(hubConfigKey);
  });

  it('posts one landing position and settles on the refetch of the project repository links', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectLandingPositionMutation());

    await mutation.mutateAsync({ projectId: 'proj_b', body: { repository: 'repo_c', after_repository: 'repo_a' } });

    const calls = stub.forRoute('/api/projects/proj_b/landing-order/position', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ repository: 'repo_c', after_repository: 'repo_a' });
    expect(invalidatedKeys(spy)).toContainEqual(hubProjectLinksKey('proj_b', 'repository'));
  });
});
