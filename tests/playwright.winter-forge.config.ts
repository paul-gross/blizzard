import * as path from 'node:path';
import { defineConfig } from '@playwright/test';
import base from './playwright.config';
import { runDir } from './harness/env';

const scratch = process.env.BLIZZARD_TMPDIR;
if (!scratch || !path.isAbsolute(scratch)) throw new Error('BLIZZARD_TMPDIR must name an absolute scratch directory');

export default defineConfig({
  ...base,
  outputDir: path.join(runDir(), 'winter-forge-results'),
  reporter: [['list']],
  projects: [
    ...base.projects!.filter((project) => ['setup-service-hub', 'teardown-service-hub'].includes(project.name!)),
    { name: 'manual-winter-forge', testDir: 'manual/winter-forge', dependencies: ['setup-service-hub'], workers: 1 },
  ],
});
