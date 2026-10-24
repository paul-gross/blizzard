import * as crypto from 'node:crypto';
import * as path from 'node:path';
import { type APIRequestContext, test as base, type TestInfo } from '@playwright/test';
import { WORKERS_PER_HUB } from '../playwright.config';
import { HubInternal } from './client/internal';
import { type CallRecorder, LATENCY_ATTACHMENT, recorderFor, recordsOf } from './client/latency';
import { Ledger } from './client/ledger';
import { stopDaemon } from './daemons/process';
import { runDir } from './env';
import { type HubCoordinates, readProjectHubs, startHubStack } from './hubs';
import { hubLog, scanLog } from './logs';
import { discoverProfiles, readProfile } from './profiles';

export const LEVELS = ['common', 'fresh-project', 'fresh-tenant', 'fresh-hub'] as const;
export type Level = (typeof LEVELS)[number];

export interface LevelInfo {
  declared: Level;
  served: Level;
}

/** The level a test is served: the one it declared, or the one above when the cross-check raises every test. */
export function resolveLevel(declared: Level | undefined, raise: boolean): LevelInfo {
  if (declared === undefined) {
    throw new Error(
      `The test sets no \`isolation\` option. Declare the loosest level its reads allow with ` +
        `test.use({ isolation: ${LEVELS.map((level) => `'${level}'`).join(' | ')} }).`,
    );
  }
  const above = LEVELS[Math.min(LEVELS.indexOf(declared) + 1, LEVELS.length - 1)] as Level;
  return { declared, served: raise ? above : declared };
}

/** The service project whose setup a project runs on: the `harness` project rides the default profile's. */
export function profileProject(projectName: string): string {
  return projectName === 'harness' ? 'service-hub' : projectName;
}

/** Whether a level shares its tenant with other tests, so reads are narrowed to what the test made. */
export function sharesTenant(level: Level): boolean {
  return level === 'common' || level === 'fresh-project';
}

/**
 * Hub `floor(parallelIndex / WORKERS_PER_HUB)` of the default profile, and hub `0` of any other. `parallelIndex` is the
 * stable slot Playwright gives each concurrent worker, so a worker that restarts after a failure lands back on the hub
 * its slot always used, which `workerIndex` would not do.
 */
export function hubFor(project: string, parallelIndex: number): HubCoordinates {
  const { hubs } = readProjectHubs(project);
  const wanted = project === 'service-hub' ? Math.floor(parallelIndex / WORKERS_PER_HUB) : 0;
  const hub = hubs[Math.min(wanted, hubs.length - 1)];
  if (hub === undefined) throw new Error(`hubs/${project}.json lists no hub`);
  return hub;
}

/** The worker's shared hub, resolved only when asked: a `fresh-hub` test never asks. */
export interface SharedHub {
  coordinates(): HubCoordinates;
  /** The worker's one request context for this hub, created with the hub's URL and disposed when the worker ends. */
  context(): Promise<APIRequestContext>;
}

/** The hub a test reaches. */
export interface TestHub {
  coordinates: HubCoordinates;
  context: APIRequestContext;
  /** Whether the hub was started for this test alone. */
  fresh: boolean;
  /** The hub's log as attached to a failing test: the tenant's lines on a shared hub, the whole log on a fresh one. */
  logTail(tenantId?: string): string;
}

export interface Tenant {
  id: string;
  name: string;
}

export interface Project {
  id: string;
  slug: string;
}

/** What the log-attaching fixture learns while the test runs: the tenant it acted in. */
export interface Touched {
  tenantId?: string;
}

export interface WorkerFixtures {
  hub: SharedHub;
  adminClient: (record?: CallRecorder) => HubInternal;
}

export interface TestFixtures {
  isolation: Level | undefined;
  level: LevelInfo;
  testHub: TestHub;
  tenant: Tenant;
  ledger: Ledger;
  touched: Touched;
  attachLogs: void;
  attachLatency: void;
}

/** A name from the test's title and a random suffix, so two runs of one test never collide. */
export function uniqueName(testInfo: TestInfo, prefix = ''): string {
  const slug = testInfo.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 24)
    .replace(/-$/, '');
  return `${prefix}${slug}-${crypto.randomBytes(3).toString('hex')}`;
}

export const tenancy = base.extend<TestFixtures, WorkerFixtures>({
  hub: [
    async ({ playwright }, use, workerInfo) => {
      const project = profileProject(workerInfo.project.name);
      const contexts = new Map<number, APIRequestContext>();
      const coordinates = (): HubCoordinates => hubFor(project, workerInfo.parallelIndex);
      await use({
        coordinates,
        context: async () => {
          const hub = coordinates();
          let context = contexts.get(hub.index);
          if (context === undefined) {
            context = await playwright.request.newContext({ baseURL: hub.url });
            contexts.set(hub.index, context);
          }
          return context;
        },
      });
      await Promise.all([...contexts.values()].map((context) => context.dispose()));
    },
    { scope: 'worker' },
  ],

  adminClient: [
    async ({ hub }, use) => {
      await use((record) => new HubInternal(hub.coordinates().url, record));
    },
    { scope: 'worker' },
  ],

  isolation: [undefined, { option: true }],

  level: async ({ isolation }, use, testInfo) => {
    const level = resolveLevel(isolation, process.env.BLIZZARD_ISOLATION_RAISE === '1');
    // The cross-check names a test by the level it declared, which the report carries only through this.
    testInfo.annotations.push({ type: 'isolation-level', description: level.declared });
    await use(level);
  },

  testHub: async ({ level, hub, playwright }, use, testInfo) => {
    if (level.served !== 'fresh-hub') {
      const coordinates = hub.coordinates();
      await use({
        coordinates,
        context: await hub.context(),
        fresh: false,
        logTail: (tenantId) => hubLog({ logPath: coordinates.logPath, tenantId: tenantId ?? '', fresh: false }).tail(),
      });
      return;
    }
    // A hub for this test alone, under the profile of the folder the test lives in.
    const folder = discoverProfiles().find((candidate) => candidate.project === profileProject(testInfo.project.name));
    const dir = path.join(runDir(), 'fresh', uniqueName(testInfo));
    const coordinates = await startHubStack(dir, 0, folder === undefined ? {} : readProfile(folder.file));
    const context = await playwright.request.newContext({ baseURL: coordinates.url });
    try {
      await use({
        coordinates,
        context,
        fresh: true,
        logTail: () => hubLog({ logPath: coordinates.logPath, tenantId: '', fresh: true }).tail(),
      });
    } finally {
      await context.dispose();
      await Promise.all(coordinates.daemons.map(stopDaemon));
    }
    // A fresh hub's whole log is read for refusals the moment its test stops it.
    const refusals = scanLog(coordinates.logPath).map((line) => line.raw);
    if (refusals.length > 0) throw new Error(`a statement guard refused outside any request:\n${refusals.join('\n')}`);
  },

  tenant: async ({ level, testHub, adminClient, touched }, use, testInfo) => {
    let tenant: Tenant;
    if (level.served === 'common' || level.served === 'fresh-project') {
      const id = testHub.coordinates.commonTenantId;
      if (id === undefined) throw new Error('the shared hub has no common tenant');
      tenant = { id, name: 'common' };
    } else if (level.served === 'fresh-tenant') {
      const created = await adminClient(recorderFor(testInfo)).createTenant(uniqueName(testInfo));
      tenant = { id: created.tenant_id, name: created.name };
    } else {
      // The fresh hub's seeded tenant, the only one open.
      const open = (await new HubInternal(testHub.coordinates.url).listTenants()).filter((candidate) => candidate.state === 'open');
      const [seeded] = open;
      if (open.length !== 1 || seeded === undefined) throw new Error(`a fresh hub should hold one open tenant, not ${open.length}`);
      tenant = { id: seeded.tenant_id, name: seeded.name };
    }
    touched.tenantId = tenant.id;
    await use(tenant);
  },

  ledger: async ({}, use) => {
    await use(new Ledger());
  },

  touched: async ({}, use) => {
    await use({});
  },

  // Runs for every test: the calls its clients made, each with its duration, attached for the reporter's summary.
  attachLatency: [
    async ({}, use, testInfo) => {
      await use();
      const calls = recordsOf(testInfo);
      if (calls.length > 0) await testInfo.attach(LATENCY_ATTACHMENT, { body: JSON.stringify(calls), contentType: 'application/json' });
    },
    { auto: true },
  ],

  // Runs for every test: a test that never set `isolation` fails here, naming the option, and a failing test has the
  // tail of the hub log it touched attached to its report.
  attachLogs: [
    async ({ testHub, touched }, use, testInfo) => {
      await use();
      if (testInfo.status === testInfo.expectedStatus) return;
      const tail = testHub.logTail(touched.tenantId);
      if (tail !== '') await testInfo.attach('hub log', { body: tail, contentType: 'text/plain' });
    },
    { auto: true },
  ],
});
