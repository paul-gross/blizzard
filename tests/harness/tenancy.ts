import * as fs from 'node:fs';
import * as path from 'node:path';
import type { APIRequestContext } from '@playwright/test';
import { WORKERS_PER_HUB } from '../playwright.config';
import { signInSuperuser, type Superuser } from './auth';
import { base, uniqueName } from './base';
import { HubInternal } from './client/internal';
import { type CallRecorder, recorderFor } from './client/latency';
import { hostHub, initHub } from './daemons/hub';
import { egressDirectory, type EgressReader, egressReader } from './egress';
import { isAlive, killLive, type LiveDaemon, stopDaemon, stopLive } from './daemons/process';
import { readContract, runDir } from './env';
import { type HubCoordinates, readProjectHubs, startStack } from './hubs';
import { hubLog, scanLog } from './logs';
import { profileProject } from './projects';
import { checkProfile, discoverProfiles, mergeProfile, profileEnv, profileEnvNames, readProfile, type TomlTable } from './profiles';

export { profileProject };

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

/** What a test's `hubConfig` may name as `${name}`: the stack's addresses, and the export directory only a hub of its own has. */
const HUB_CONFIG_PLACEHOLDERS = ['idp', 'forge', 'egress'];

/**
 * What a test may hand a hub of its own, and only at `fresh-hub`: `hubConfig` and `hubEnv` are refused anywhere else, a
 * misspelled `hubConfig` key and a `hubEnv` name the environment contract or a profile sets are refused everywhere. Throws before
 * any hub starts.
 */
export function requireHubOptions(served: Level, hubConfig: TomlTable, hubEnv: Record<string, string>, contractSet: string[] = Object.keys(readContract().set), ownedByProfile: string[] = profileEnvNames()): void {
  const named = [...(Object.keys(hubConfig).length > 0 ? ['hubConfig'] : []), ...(Object.keys(hubEnv).length > 0 ? ['hubEnv'] : [])];
  if (named.length > 0 && served !== 'fresh-hub') {
    throw new Error(`${named.join(' and ')} reach a hub of the test's own, and this test is served "${served}". Declare isolation "fresh-hub".`);
  }
  const problems = checkProfile(hubConfig, { placeholders: [...HUB_CONFIG_PLACEHOLDERS] }).map((problem) => `hubConfig ${problem}`);
  for (const name of Object.keys(hubEnv)) {
    if (contractSet.includes(name)) problems.push(`hubEnv ${name}: the environment contract (daemon-env.json) sets it, so a test cannot change it`);
    if (ownedByProfile.includes(name)) problems.push(`hubEnv ${name}: a hub profile sets it (harness/profiles.ts), so a test cannot change it`);
  }
  if (problems.length > 0) throw new Error(`the hub options are unsound:\n${problems.join('\n')}`);
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
  /**
   * The worker's one request context for this hub, created with the hub's URL and disposed when the worker ends. On a
   * hub with authentication on it carries the superuser's session.
   */
  context(): Promise<APIRequestContext>;
}

/** A fresh hub's process, stopped, killed, and started again under the URL it first held. */
export interface FreshHub {
  /** The pid of the hub process now running, or last run. */
  readonly pid: number;
  /** `SIGTERM`, then `SIGKILL` after the grace; resolves with the exit code, or the signal that ended it. */
  stop(): Promise<number | string>;
  /** `SIGKILL`; resolves once the process is gone. */
  kill(): Promise<void>;
  /** Hosts the stopped hub again from its directory, with the same config and `hubEnv`, on the same URL. */
  start(): Promise<HubCoordinates>;
  /**
   * Replaces the stopped hub's data with a freshly initialized hub's, under the same profile, `hubConfig` and `hubEnv`,
   * and hosts it on the same URL: every tenant, runner and token the old data held is gone, and the one seeded tenant
   * has a new id. Throws when the hub is running.
   */
  reset(): Promise<HubCoordinates>;
}

/** A fresh hub's lifecycle, and the one place that knows which process is the hub's now. */
interface FreshHubControl extends FreshHub {
  current(): LiveDaemon;
}

/** The hub a test reaches. */
export interface TestHub {
  coordinates: HubCoordinates;
  /** On a hub with authentication on, the context carries the superuser's session once `superuser()` has resolved. */
  context: APIRequestContext;
  /**
   * The superuser credential of a hub with authentication on, `undefined` on one without. A shared hub's is the session
   * the setup project recorded; a fresh hub's is a walk made on first demand, in `context`, and remembered.
   */
  superuser(): Promise<Superuser | undefined>;
  /** Whether the hub was started for this test alone. */
  fresh: boolean;
  /** A fresh hub's lifecycle; absent on a shared hub. */
  lifecycle?: FreshHub;
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
  /** TOML for the fresh hub's `blizzard-hub.toml`, in parsed-profile shape (integers as `bigint`); `fresh-hub` only. */
  hubConfig: TomlTable;
  /** Environment for the fresh hub's process; `fresh-hub` only. */
  hubEnv: Record<string, string>;
  /**
   * Reads the export directory a fresh hub writes, which the test's `hubConfig` names as `${egress}`; `fresh-hub` only.
   */
  egress: EgressReader;
  level: LevelInfo;
  freshHub: FreshHub;
  testHub: TestHub;
  /** The test's tenant, resolved on first call: a test that never asks never forces the superuser's sign-in. */
  tenantOf: () => Promise<Tenant>;
  tenant: Tenant;
  touched: Touched;
  attachLogs: void;
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
            context = await playwright.request.newContext({
              baseURL: hub.url,
              extraHTTPHeaders: hub.auth?.session === undefined ? {} : { cookie: `bz_session=${hub.auth.session}` },
            });
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
      await use((record) => {
        const coordinates = hub.coordinates();
        return new HubInternal(coordinates.url, record, coordinates.auth?.session);
      });
    },
    { scope: 'worker' },
  ],

  isolation: [undefined, { option: true }],

  hubConfig: [{}, { option: true }],
  hubEnv: [{}, { option: true }],

  level: async ({ isolation, hubConfig, hubEnv }, use, testInfo) => {
    const level = resolveLevel(isolation, process.env.BLIZZARD_ISOLATION_RAISE === '1');
    requireHubOptions(level.served, hubConfig, hubEnv);
    // The cross-check names a test by the level it declared, which the report carries only through this.
    testInfo.annotations.push({ type: 'isolation-level', description: level.declared });
    await use(level);
  },

  testHub: async ({ level, hub, playwright, hubConfig, hubEnv }, use, testInfo) => {
    if (level.served !== 'fresh-hub') {
      const coordinates = hub.coordinates();
      await use({
        coordinates,
        context: await hub.context(),
        superuser: async () => {
          if (coordinates.auth === undefined) return undefined;
          if (coordinates.auth.session === undefined || coordinates.auth.userId === undefined) {
            throw new Error('the hubs file records no superuser session for this hub; the setup project signs it in');
          }
          return { session: coordinates.auth.session, userId: coordinates.auth.userId };
        },
        fresh: false,
        logTail: (tenantId) => hubLog({ logPath: coordinates.logPath, tenantId: tenantId ?? '', fresh: false }).tail(),
      });
      return;
    }
    // A hub for this test alone, under the profile of the folder the test lives in, the test's `hubConfig` over it.
    const folder = discoverProfiles().find((candidate) => candidate.project === profileProject(testInfo.project.name));
    const dir = path.join(runDir(), 'fresh', uniqueName(testInfo));
    const profile = mergeProfile(folder === undefined ? {} : readProfile(folder.file), hubConfig);
    // The profile's own environment over the test's, at every start of this hub: the fence is the profile's alone.
    const env = { ...hubEnv, ...profileEnv(folder?.name ?? '') };
    const stack = await startStack(dir, 0, profile, env);
    const { coordinates } = stack;
    let hubProcess = stack.hub;
    const control: FreshHubControl = {
      current: () => hubProcess,
      get pid() {
        return hubProcess.pid;
      },
      stop: () => stopLive(hubProcess),
      kill: async () => {
        await killLive(hubProcess);
      },
      start: async () => {
        if (isAlive(hubProcess.pid)) throw new Error('the fresh hub is running; stop() or kill() it before start()');
        hubProcess = await hostHub(dir, { env, port: Number(new URL(coordinates.url).port) });
        return coordinates;
      },
      reset: async () => {
        if (isAlive(hubProcess.pid)) throw new Error('the fresh hub is running; stop() or kill() it before reset()');
        fs.rmSync(path.join(dir, 'hub'), { recursive: true, force: true });
        await initHub(dir, stack.profile);
        hubProcess = await hostHub(dir, { env, port: Number(new URL(coordinates.url).port) });
        return coordinates;
      },
    };
    const context = await playwright.request.newContext({ baseURL: coordinates.url });
    let superuser: Promise<Superuser | undefined> | undefined;
    try {
      await use({
        coordinates,
        context,
        superuser: () => (superuser ??= coordinates.auth === undefined ? Promise.resolve(undefined) : signInSuperuser(coordinates, context)),
        fresh: true,
        lifecycle: control,
        logTail: () => hubLog({ logPath: coordinates.logPath, tenantId: '', fresh: true }).tail(),
      });
    } finally {
      await context.dispose();
      // The hub's process now, not the one the stack started: a `start()` replaced it.
      await stopDaemon(control.current());
      await Promise.all(coordinates.daemons.slice(1).map(stopDaemon));
    }
    // A fresh hub's whole log is read for refusals the moment its test stops it.
    const refusals = scanLog(coordinates.logPath).map((line) => line.raw);
    if (refusals.length > 0) throw new Error(`a statement guard refused outside any request:\n${refusals.join('\n')}`);
  },

  freshHub: async ({ level, testHub }, use) => {
    if (testHub.lifecycle === undefined) {
      throw new Error(`freshHub is the lifecycle of a hub of the test's own, and this test is served "${level.served}". Declare isolation "fresh-hub".`);
    }
    await use(testHub.lifecycle);
  },

  egress: async ({ level, testHub }, use) => {
    if (!testHub.fresh) throw new Error(`egress reads the export directory of a hub of the test's own, and this test is served "${level.served}". Declare isolation "fresh-hub".`);
    await use(egressReader(egressDirectory(path.dirname(testHub.coordinates.logPath))));
  },

  tenantOf: async ({ level, testHub, adminClient, touched }, use, testInfo) => {
    const resolve = async (): Promise<Tenant> => {
      let tenant: Tenant;
      if (level.served === 'common' || level.served === 'fresh-project') {
        const id = testHub.coordinates.commonTenantId;
        if (id === undefined) throw new Error('the shared hub has no common tenant');
        tenant = { id, name: 'common' };
      } else if (level.served === 'fresh-tenant') {
        const created = await adminClient(recorderFor(testInfo)).createTenant(uniqueName(testInfo), (await testHub.superuser())?.userId);
        tenant = { id: created.tenant_id, name: created.name };
      } else {
        // The fresh hub's seeded tenant, the only one open.
        const admin = new HubInternal(testHub.coordinates.url, undefined, (await testHub.superuser())?.session);
        const open = (await admin.listTenants()).filter((candidate) => candidate.state === 'open');
        const [seeded] = open;
        if (open.length !== 1 || seeded === undefined) throw new Error(`a fresh hub should hold one open tenant, not ${open.length}`);
        // Tenant creation mints the packaged graphs into a tenant at every level below; the seeded tenant has them only
        // once `graph sync` runs, as it does after every deploy.
        const synced = await admin.syncGraphs();
        if (!synced.ok) throw new Error(`graph sync failed on the fresh hub: ${JSON.stringify(synced.tenants)}`);
        tenant = { id: seeded.tenant_id, name: seeded.name };
      }
      touched.tenantId = tenant.id;
      return tenant;
    };
    let resolved: Promise<Tenant> | undefined;
    await use(() => (resolved ??= resolve()));
  },

  tenant: async ({ tenantOf }, use) => {
    await use(await tenantOf());
  },

  touched: async ({}, use) => {
    await use({});
  },

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
