import * as fs from 'node:fs';
import { type APIRequestContext, expect, mergeTests } from '@playwright/test';
import { loginThroughCli } from './login';
import { type People, type Person, peopleFactory } from './people';
import { type CliResult, CliTimeout, type Prompt, runCommand } from './cli';
import { forgeApi, type ForgeApi, type HubApi, hubApi, idpApi, mockRunnerApi } from './client/clients';
import { fleetApi, type Verbs } from './client/engine';
import type { Role, RunnerAddRequest, RunnerDeclining } from './client/wire/hub/types.gen';
import { type Events, eventStream, type Subscription } from './events';
import {
  type FactoryContext,
  type FiledIssue,
  issueFactory,
  type MockRunner,
  mockRunnerFactory,
  type Repository,
  repositoryFactory,
  requireOwnTenant,
  type RunnerOptions,
} from './factories';
import { binDir, daemonEnv, requireBinary } from './env';
import { type HubLog, hubLog as readHubLog } from './logs';
import { uniqueName } from './base';
import { mockWorld } from './mock-world';
import { type RunnerBinding, runnerWorld } from './runner-world';
import { type Project, sharesTenant, tenancy } from './tenancy';

/** A fleet-plane client for one runner, under the token minted for it. */
export interface FleetClient extends Verbs {
  runnerId: string;
  token: string;
}

interface Fixtures {
  api: HubApi;
  /** Internal: the runner fixtures' hub, the test's own tenant on the test's hub; a host needs isolation `fresh-tenant` or above. */
  runnerBinding: RunnerBinding;
  /** The role `user` is invited as; `test.use({ userRole })`. */
  userRole: Role;
  /** One signed-in person in `tenant` with the role `userRole` names. Needs a hub with authentication on. */
  user: Person;
  /** Signs people in through the stub IdP: `create` for further invited people, `signIn` for an identity the test names. */
  people: People;
  /** A session-free hub client; its context is independent of the signed-in admin and refused sign-in walks. */
  anonymous: HubApi;
  /** Stub IdP controls, available only on a hub owned by this test. */
  idp: ReturnType<typeof idpApi>;
  project: Project;
  /** A client sending only `Authorization: Bearer <token>`, on a context holding no cookie, so the token alone authenticates. */
  bearer: (token: string) => Promise<Verbs>;
  fleet: (name: string, options?: RunnerOptions) => Promise<FleetClient>;
  forge: ForgeApi;
  events: Events;
  hubLog: HubLog;
  /**
   * Runs the real `blizzard` CLI against the test's hub and tenant, with a config home of its own. With `as`, the CLI
   * acts as that person: the first call for a person logs them in (`hub login --paste`) in a config home of theirs.
   */
  cli: (args: string[], options?: { as?: Person }) => Promise<CliResult>;
  issue: (title: string, options?: { repository?: Repository }) => Promise<FiledIssue>;
  repository: (name?: string) => Promise<Repository>;
  mockRunner: (name: string, options?: RunnerOptions) => Promise<MockRunner>;
  /** Internal: what the factories share. */
  factoryContext: FactoryContext;
  /** Internal: the event subscriptions a test opened, closed when it ends. */
  openedSubscriptions: Subscription[];
}

/** The hub's wire types, for a spec that builds a typed literal: `satisfies Wire.WorkSourceDocument`. */
export type * as Wire from './client/wire/hub/types.gen';
export type { ForgeApi, HubApi };
export type { Repository };

/** The package's `test` and `expect`, and the wire types: the only module a spec imports. */
export const test = mergeTests(tenancy, runnerWorld).extend<Fixtures>({
  api: async ({ testHub, tenant, level, ledger }, use, testInfo) => {
    await use(
      hubApi({
        context: testHub.context,
        testInfo,
        tenantId: tenant.id,
        narrow: sharesTenant(level.served),
        level: level.served,
        ledger,
        logContext: () => testHub.logTail(tenant.id),
      }),
    );
  },

  userRole: ['contributor', { option: true }],

  people: async ({ testHub, tenantOf, ledger, level, playwright }, use, testInfo) => {
    const { people, disposeAll } = peopleFactory({
      testHub,
      tenantOf,
      ledger,
      testInfo,
      level: level.served,
      newContext: () => playwright.request.newContext({ baseURL: testHub.coordinates.url }),
      unique: (prefix) => uniqueName(testInfo, prefix),
    });
    await use(people);
    await disposeAll();
  },

  anonymous: async ({ testHub, level, ledger, playwright }, use, testInfo) => {
    const context = await playwright.request.newContext({ baseURL: testHub.coordinates.url });
    try {
      await use(hubApi({ context, testInfo, narrow: false, level: level.served, ledger, logContext: () => testHub.logTail() }));
    } finally {
      await context.dispose();
    }
  },

  idp: async ({ testHub, level, ledger }, use, testInfo) => {
    await use(idpApi({ context: testHub.context, testInfo, url: testHub.coordinates.idpUrl, level: level.served, ledger }));
  },

  user: async ({ people, userRole }, use) => {
    await use(await people.create({ role: userRole }));
  },

  runnerBinding: async ({ testHub, tenant, tenantOf, level, ledger, playwright }, use, testInfo) => {
    const hub = testHub.coordinates;
    // On a hub offering an IdP, `init` runs under a logged-in operator's config home; the host's own stays empty.
    // Built only then, since the `people` fixture itself refuses a hub with authentication off.
    let operator: Promise<string> | undefined;
    let disposePeople: (() => Promise<void>) | undefined;
    const operatorHome = async (): Promise<string> => {
      const made = peopleFactory({
        testHub,
        tenantOf,
        ledger,
        testInfo,
        level: level.served,
        newContext: () => playwright.request.newContext({ baseURL: hub.url }),
        unique: (prefix) => uniqueName(testInfo, prefix),
      });
      disposePeople = made.disposeAll;
      const person = await made.people.create({ role: 'admin' });
      const home = testInfo.outputPath('operator-config-home');
      const env = daemonEnv({ BZ_HUB_URL: hub.url, BZ_HUB_TENANT: tenant.id, PYTHONUNBUFFERED: '1' }, home);
      const run = (args: string[], runEnv: NodeJS.ProcessEnv, prompt?: Prompt): Promise<CliResult> =>
        runCommand(requireBinary(binDir(), 'blizzard'), args, { env: runEnv, prompt });
      await loginThroughCli({ run, env, person, hub });
      return home;
    };
    await use({
      hub: {
        url: hub.url,
        tenantId: tenant.id,
        operatorHome: hub.auth === undefined ? undefined : () => (operator ??= operatorHome()),
      },
      admit: () => requireOwnTenant(level.served, 'runnerHost()'),
    });
    await disposePeople?.();
  },

  bearer: async ({ testHub, ledger, playwright }, use, testInfo) => {
    const made: APIRequestContext[] = [];
    await use(async (token) => {
      const context = await playwright.request.newContext({ baseURL: testHub.coordinates.url });
      made.push(context);
      return fleetApi({ context, testInfo, token, ledger, logContext: () => '' });
    });
    await Promise.all(made.map((context) => context.dispose()));
  },

  project: async ({ level, tenant, api }, use, testInfo) => {
    if (level.served === 'fresh-project') {
      const slug = uniqueName(testInfo, 'p-');
      const created = await api.post('/api/projects', { data: { slug, name: slug } });
      if (!created.ok) throw new Error(`creating project ${slug} answered ${created.status}: ${JSON.stringify(created.body)}`);
      await use({ id: created.body.project_id as string, slug });
      return;
    }
    // The tenant's `default` project; `unfiltered`, because the ledger holds no project the test did not create.
    const listed = await api.unfiltered.get('/api/projects');
    const found = (listed.body.projects as { project_id: string; slug: string }[]).find((candidate) => candidate.slug === 'default');
    if (found === undefined) throw new Error(`tenant ${tenant.id} has no default project`);
    await use({ id: found.project_id, slug: found.slug });
  },

  fleet: async ({ api, testHub, tenant, level, ledger }, use, testInfo) => {
    await use(async (name, options) => {
      requireOwnTenant(level.served, 'fleet()');
      const minted = await api.post('/api/runners', { data: { name, ...options } satisfies RunnerAddRequest });
      if (!minted.ok) throw new Error(`minting runner ${name} answered ${minted.status}: ${JSON.stringify(minted.body)}`);
      ledger.add('runner', minted.body.runner_id as string);
      const client = fleetApi({
        context: testHub.context,
        testInfo,
        token: minted.body.token as string,
        ledger,
        logContext: () => testHub.logTail(tenant.id),
      });
      return { ...client, runnerId: minted.body.runner_id as string, token: minted.body.token as string };
    });
  },

  forge: async ({ testHub, ledger }, use, testInfo) => {
    await use(forgeApi({ context: testHub.context, testInfo, url: testHub.coordinates.forgeUrl, ledger }));
  },

  openedSubscriptions: async ({}, use) => {
    const opened: Subscription[] = [];
    await use(opened);
    for (const subscription of opened) subscription.close();
  },

  events: async ({ tenant, testHub, level, ledger, openedSubscriptions, api, issue }, use) => {
    await use(
      eventStream({
        hubUrl: testHub.coordinates.url,
        tenantId: tenant.id,
        ledger,
        narrow: sharesTenant(level.served),
        fromNow: sharesTenant(level.served),
        opened: openedSubscriptions,
        superuserSession: async () => (await testHub.superuser())?.session,
        barrier: {
          file: async () => (await issue('barrier')).chunk.chunk_id,
          edit: async (chunkId, effort) => {
            const edited = await api.patch(`/api/chunks/${chunkId}`, { data: { default_effort: effort } });
            if (edited.status !== 202) throw new Error(`editing barrier chunk ${chunkId} answered ${edited.status}: ${JSON.stringify(edited.body)}`);
          },
        },
      }),
    );
  },

  hubLog: async ({ tenant, level, testHub }, use) => {
    await use(readHubLog({ logPath: testHub.coordinates.logPath, tenantId: tenant.id, fresh: level.served === 'fresh-hub' }));
  },

  cli: async ({ testHub, tenant }, use, testInfo) => {
    const run = async (args: string[], env: NodeJS.ProcessEnv, prompt?: Prompt): Promise<CliResult> => {
      try {
        return await runCommand(requireBinary(binDir(), 'blizzard'), args, { env, prompt });
      } catch (error) {
        if (error instanceof CliTimeout) {
          await testInfo.attach('cli stdout', { body: error.stdout, contentType: 'text/plain' });
          await testInfo.attach('cli stderr', { body: error.stderr, contentType: 'text/plain' });
        }
        throw error;
      }
    };
    const envFor = (home: string): NodeJS.ProcessEnv => {
      fs.mkdirSync(home, { recursive: true });
      // Unbuffered, so the login URL reaches the harness before the command waits on the code.
      return daemonEnv({ BZ_HUB_URL: testHub.coordinates.url, BZ_HUB_TENANT: tenant.id, PYTHONUNBUFFERED: '1' }, home);
    };
    // One login per person per test, in the config home that person's later commands read.
    const logins = new Map<string, Promise<void>>();
    const login = (person: Person, env: NodeJS.ProcessEnv): Promise<void> => {
      let done = logins.get(person.userId);
      if (done === undefined) {
        done = loginThroughCli({ run, env, person, hub: testHub.coordinates });
        logins.set(person.userId, done);
      }
      return done;
    };
    await use(async (args, options = {}) => {
      const person = options.as;
      const env = envFor(testInfo.outputPath(person === undefined ? 'cli-config-home' : `cli-config-home-${person.username}`));
      if (person !== undefined) await login(person, env);
      return run(args, env);
    });
  },

  factoryContext: async ({ api, forge, project, level, testHub, ledger }, use, testInfo) => {
    await use({ api, forge, project, level: level.served, testHub, ledger, unique: (prefix) => uniqueName(testInfo, prefix),
      mockClient: (url) => mockRunnerApi({ context: testHub.context, testInfo, url, ledger }) });
  },

  repository: async ({ factoryContext }, use) => {
    await use(repositoryFactory(factoryContext));
  },

  issue: async ({ factoryContext, repository }, use) => {
    await use(issueFactory(factoryContext, repository));
  },

  mockRunner: async ({ factoryContext }, use) => {
    const { factory, stopAll } = mockRunnerFactory(factoryContext);
    await use(factory);
    await stopAll();
  },
});

/** The runner world's `test`: a mock hub the test owns and runner clients, with no shared hub in reach. */
export const runnerTest = mockWorld;

/** The runner host's wire types, for a spec that builds a typed literal: `satisfies RunnerWire.FactView`. */
export type * as RunnerWire from './client/wire/runner/types.gen';
export type { TranscriptSegmentRecord } from './client/wire/hub/types.gen';
export type { OtlpSink, ReceivedItem, ReceivedSpan } from './otlp-sink';
export type { AuthStub } from './auth-stub';
export type { MockHub } from './mock-world';
export type { RunnerHosts } from './runner-world';
export type { RunnerHost, RunnerHostOptions } from './runner-host';
export { HOLD_OPEN, holdingChunk, runningLeaseWithWorker, seededLeaseWithWorker, stepUntil } from './runner-steps';
export type { VendorUsage } from './vendor-usage';
export type { WorkerCheckpoint } from './worker-checkpoint';
export { expect };
export { packagedSystemArtifact } from './system-artifacts';
export { chunkTraceId } from './trace-ids';
export { claudeCodeExports } from './telemetry-bodies';
/** The wire shape of one reported decline reason, for a spec to type its literals against. */
export type { RunnerDeclining };
export type { MockRunner };
export type { Barrier, BarrierSubscription, Frame, Subscription } from './events';
export type { MockHubClient } from './client/mock-hub';
export type { MockChunkSpec } from './client/mock';
export type { HostRoutes, RunnerClient, RunnerHostClient, TickPass } from './client/runner';
