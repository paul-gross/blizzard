import {
  projectView,
  type RequestClientStub,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { QueryClient, QueryObserver } from '@tanstack/angular-query-experimental';

import { ensureProjectDomains, projectDomainsQueryOptions, readProject, readProjects } from './projects.query';
import { hubProjectsKey } from './query-keys';

const BLIZZARD = projectView({ created_at: '2026-10-01T00:00:00Z' });

describe('the projects reads', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('lists the tenant’s projects', async () => {
    stub = stubHubClient({
      'GET /api/projects': () => ({ projects: [BLIZZARD] }),
    });

    await expect(readProjects()).resolves.toEqual([BLIZZARD]);
  });

  it('ensures the cached read and hands it back as domain projects', async () => {
    stub = stubHubClient({
      'GET /api/projects': () => ({ projects: [BLIZZARD] }),
    });

    const projects = await ensureProjectDomains(new QueryClient());
    expect(projects).toEqual([BLIZZARD]);
    expect(projects[0]).not.toBe(BLIZZARD);
  });

  it('shares the wire cache with domain observers and lens resolution', async () => {
    stub = stubHubClient({
      'GET /api/projects': () => ({ projects: [BLIZZARD] }),
    });
    const client = new QueryClient();
    const domain = projectDomainsQueryOptions();
    const observer = new QueryObserver(client, domain);
    const unsubscribe = observer.subscribe(vi.fn());
    const lensProjects = await ensureProjectDomains(client);
    const cached = client.getQueryData<readonly typeof BLIZZARD[]>(hubProjectsKey);

    expect(domain.queryKey).toEqual(hubProjectsKey);
    expect(observer.getCurrentResult().data).toEqual(lensProjects);
    expect(lensProjects).toEqual([BLIZZARD]);
    expect(lensProjects[0]).not.toBe(cached?.[0]);
    expect(client.getQueryData(domain.queryKey)).toBe(cached);
    expect(stub.forRoute('/api/projects', 'GET')).toHaveLength(1);
    unsubscribe();
  });

  it('reads one project by the segment a URL names, and answers none for a project the tenant does not hold', async () => {
    stub = stubHubClient({
      'GET /api/projects/{project}': (_request, { project }) =>
        project === 'old-blizzard'
          ? BLIZZARD
          : stubError(404, {
              type: 'urn:blizzard:problem:project-not-found',
              detail: 'no such project',
            }),
    });

    await expect(readProject('old-blizzard')).resolves.toEqual(BLIZZARD);
    await expect(readProject('gone')).resolves.toBeNull();
  });

  it('throws any other failure', async () => {
    stub = stubHubClient({
      'GET /api/projects/{project}': () => stubError(500, { detail: 'boom' }),
    });

    await expect(readProject('blizzard')).rejects.toBeTruthy();
  });
});
