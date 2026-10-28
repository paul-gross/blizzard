import * as crypto from 'node:crypto';
import { defineConfig } from '@playwright/test';
import { discoverProfiles, SERVICE_HUB_DIR } from './harness/profiles';
import { fleetProfileFolders, serviceProjects } from './harness/projects';

/** A run of up to this many workers shares one hub of the default profile. */
export const WORKERS_PER_HUB = 8;

// Minted once, written back into the environment so the config's re-evaluation in every worker reads the same id.
process.env.BLIZZARD_RUN_ID ??= `${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}-${crypto.randomBytes(3).toString('hex')}`;

const profiles = discoverProfiles();

export default defineConfig({
  testDir: SERVICE_HUB_DIR,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  outputDir: 'test-results',
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
    ...(process.env.CI ? ([['json', { outputFile: 'test-results/report.json' }]] as const) : []),
    ['./harness/reporter.ts'],
  ],
  projects: [
    ...serviceProjects(profiles, fleetProfileFolders(profiles)),
    {
      // The harness's own proof runs on the default profile's setup and is selected by name, never by `service-*`.
      name: 'harness',
      testDir: 'harness/checks',
      // The auth checks run under their own project, on the auth profile's hubs; the runner checks under theirs, on no hub.
      testIgnore: ['**/checks/auth/**', '**/checks/runner/**'],
      dependencies: ['setup-service-hub'],
    },
    {
      name: 'harness-auth',
      testDir: 'harness/checks/auth',
      dependencies: ['setup-service-hub-auth'],
      workers: 1,
    },
    {
      // A runner-only test owns its mock hub and starts no real hub, so this project has no setup and no dependencies:
      // a check that reached a hub-world fixture would fail for want of a hubs file.
      name: 'harness-runner',
      testDir: 'harness/checks/runner',
    },
  ],
});
