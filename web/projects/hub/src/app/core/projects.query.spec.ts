import { hubClient } from 'fleet';
import { type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';

import { readProject, readProjects } from './projects.query';

const BLIZZARD = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
};

describe('the projects reads', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('lists the tenant’s projects', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/projects' ? { projects: [BLIZZARD] } : {}));

    await expect(readProjects()).resolves.toEqual([BLIZZARD]);
  });

  it('reads one project by the segment a URL names, and answers none for a project the tenant does not hold', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/projects/old-blizzard') return BLIZZARD;
      return stubError(404, { detail: 'no such project' });
    });

    await expect(readProject('old-blizzard')).resolves.toEqual(BLIZZARD);
    await expect(readProject('gone')).resolves.toBeNull();
  });

  it('throws any other failure', async () => {
    stub = stubRequestClient(hubClient, () => stubError(500, { detail: 'boom' }));

    await expect(readProject('blizzard')).rejects.toBeTruthy();
  });
});
