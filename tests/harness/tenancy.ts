import { test as base } from '@playwright/test';
import { WORKERS_PER_HUB } from '../playwright.config';
import { type HubCoordinates, readProjectHubs } from './hubs';

/** The service project whose setup a project runs on: the `harness` project rides the default profile's. */
export function profileProject(projectName: string): string {
  return projectName === 'harness' ? 'service-hub' : projectName;
}

/**
 * The hub a worker reaches: hub `floor(parallelIndex / WORKERS_PER_HUB)` of the default profile, and hub `0` of any
 * other. `parallelIndex` is the stable slot Playwright gives each concurrent worker, so a worker that restarts after a
 * failure lands back on the hub its slot always used, which `workerIndex` would not do.
 */
export function hubFor(project: string, parallelIndex: number): HubCoordinates {
  const { hubs } = readProjectHubs(project);
  const wanted = project === 'service-hub' ? Math.floor(parallelIndex / WORKERS_PER_HUB) : 0;
  const hub = hubs[Math.min(wanted, hubs.length - 1)];
  if (hub === undefined) throw new Error(`hubs/${project}.json lists no hub`);
  return hub;
}

export interface WorkerFixtures {
  hub: HubCoordinates;
}

export const tenancy = base.extend<object, WorkerFixtures>({
  hub: [
    async ({}, use, workerInfo) => {
      await use(hubFor(profileProject(workerInfo.project.name), workerInfo.parallelIndex));
    },
    { scope: 'worker' },
  ],
});
