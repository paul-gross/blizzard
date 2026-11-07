import {
  projectView,
  type RequestClientStub,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { readProject, readProjects } from './projects.query';

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
