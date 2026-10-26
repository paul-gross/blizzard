import * as fs from 'node:fs';
import * as path from 'node:path';
import { test } from '@playwright/test';
import { WORKERS_PER_HUB } from '../../playwright.config';
import { stopDaemon, sleep } from '../daemons/process';
import { runDir } from '../env';
import { signInSuperuser } from '../auth';
import { HubInternal } from '../client/internal';
import { furnishCommonTenant, hubsFile, type HubCoordinates, type ProjectHubs, startHubStack } from '../hubs';
import { assertProfilesSound, discoverProfiles, readProfile } from '../profiles';

const FIRST_WAIT_MS = 120_000;

/**
 * Checks every profile in the package, once per run: the setup project that creates `first.lock` runs the check and
 * writes `first.done`, and the others wait on that file, so a broken profile fails every setup before any hub starts.
 */
async function checkProfilesOnce(): Promise<void> {
  const dir = runDir();
  fs.mkdirSync(dir, { recursive: true });
  const lock = path.join(dir, 'first.lock');
  const done = path.join(dir, 'first.done');
  try {
    fs.closeSync(fs.openSync(lock, 'wx'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    const deadline = Date.now() + FIRST_WAIT_MS;
    while (!fs.existsSync(done)) {
      if (Date.now() > deadline) throw new Error('the first setup project never finished the profile check', { cause: error });
      await sleep(100);
    }
    const outcome = JSON.parse(fs.readFileSync(done, 'utf8')) as { error?: string };
    if (outcome.error !== undefined) throw new Error(outcome.error, { cause: error });
    return;
  }
  try {
    assertProfilesSound();
    fs.writeFileSync(done, JSON.stringify({}));
  } catch (error) {
    fs.writeFileSync(done, JSON.stringify({ error: (error as Error).message }));
    throw error;
  }
}

test('start the profile’s shared hubs', async ({ playwright }, testInfo) => {
  testInfo.setTimeout(180_000);
  const project = testInfo.project.name.replace(/^setup-/, '');
  const folder = discoverProfiles().find((candidate) => candidate.project === project);
  if (folder === undefined) throw new Error(`no profile folder for project ${project}`);

  await checkProfilesOnce();

  // The default profile gets a hub per WORKERS_PER_HUB workers; any other profile gets one, run by one worker.
  const count = folder.name === '' ? Math.ceil(testInfo.config.workers / WORKERS_PER_HUB) : 1;
  const profile = readProfile(folder.file);
  const started = await Promise.allSettled(
    Array.from({ length: count }, (_, index) => startHubStack(path.join(runDir(), project, `hub-${index}`), index, profile)),
  );
  const hubs = started.flatMap((outcome) => (outcome.status === 'fulfilled' ? [outcome.value] : []));
  const failure = started.find((outcome) => outcome.status === 'rejected');
  if (failure !== undefined) {
    await Promise.all(hubs.flatMap((hub) => hub.daemons).map(stopDaemon));
    throw (failure as PromiseRejectedResult).reason;
  }

  const furnished: HubCoordinates[] = [];
  try {
    for (const hub of hubs) {
      if (hub.auth === undefined) {
        furnished.push({ ...hub, commonTenantId: await furnishCommonTenant(hub, new HubInternal(hub.url)) });
        continue;
      }
      // Authentication is on: the superuser signs in, which claims the bootstrap, and creates the common tenant as its admin.
      const context = await playwright.request.newContext({ baseURL: hub.url });
      try {
        const superuser = await signInSuperuser(hub, context);
        const commonTenantId = await furnishCommonTenant(hub, new HubInternal(hub.url, undefined, superuser.session), superuser.userId);
        furnished.push({ ...hub, commonTenantId, auth: { ...hub.auth, session: superuser.session, userId: superuser.userId } });
      } finally {
        await context.dispose();
      }
    }
  } catch (error) {
    await Promise.all(hubs.flatMap((hub) => hub.daemons).map(stopDaemon));
    throw error;
  }

  const record: ProjectHubs = { project, hubs: furnished };
  fs.mkdirSync(path.dirname(hubsFile(project)), { recursive: true });
  fs.writeFileSync(hubsFile(project), JSON.stringify(record, null, 2));
});
