import * as fs from 'node:fs';
import { expect, test } from '@playwright/test';
import { stopDaemon } from '../daemons/process';
import { hubsFile, type ProjectHubs } from '../hubs';
import { scanLog } from '../logs';

test('stop the profile’s shared hubs and read their logs for refusals', async ({}, testInfo) => {
  testInfo.setTimeout(120_000);
  const project = testInfo.project.name.replace(/^teardown-/, '');
  const file = hubsFile(project);
  // A setup that failed before writing its coordinates stopped what it had started.
  if (!fs.existsSync(file)) return;
  const { hubs } = JSON.parse(fs.readFileSync(file, 'utf8')) as ProjectHubs;

  await Promise.all(hubs.flatMap((hub) => hub.daemons).map(stopDaemon));

  // Whole logs, read after the stop, so a refusal written after the last test still counts.
  const refusals = hubs.flatMap((hub) => scanLog(hub.logPath).map((line) => `${hub.logPath}: ${line.raw}`));
  expect(refusals, 'a statement guard refused outside any request').toEqual([]);
});
