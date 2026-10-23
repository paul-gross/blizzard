import * as crypto from 'node:crypto';
import * as path from 'node:path';
import { defineConfig, type PlaywrightTestConfig } from '@playwright/test';
import { discoverProfiles, SERVICE_HUB_DIR } from './harness/profiles';

/** A run of up to this many workers shares one hub of the default profile. */
export const WORKERS_PER_HUB = 8;

// Minted once, written back into the environment so the config's re-evaluation in every worker reads the same id.
process.env.BLIZZARD_RUN_ID ??= `${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}-${crypto.randomBytes(3).toString('hex')}`;

type Project = NonNullable<PlaywrightTestConfig['projects']>[number];

const profiles = discoverProfiles();
const profileFolders = profiles.filter((profile) => profile.name !== '');

function serviceProjects(): Project[] {
  return profiles.flatMap((profile): Project[] => {
    const isDefault = profile.name === '';
    return [
      {
        name: `setup-${profile.project}`,
        testDir: 'harness/setup',
        testMatch: 'setup.spec.ts',
        teardown: `teardown-${profile.project}`,
      },
      { name: `teardown-${profile.project}`, testDir: 'harness/setup', testMatch: 'teardown.spec.ts' },
      {
        name: profile.project,
        testDir: profile.dir,
        // The default project ignores every profile subfolder, so a spec runs under exactly one project.
        testIgnore: isDefault ? profileFolders.map((folder) => path.join(folder.dir, '**')) : undefined,
        dependencies: [`setup-${profile.project}`],
        // A hub-wide setting only a few tests need costs one hub, run by one worker in sequence.
        workers: isDefault ? undefined : 1,
      },
    ];
  });
}

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
    ...serviceProjects(),
    {
      // The harness's own proof runs on the default profile's setup and is selected by name, never by `service-*`.
      name: 'harness',
      testDir: 'harness/checks',
      dependencies: ['setup-service-hub'],
    },
  ],
});
