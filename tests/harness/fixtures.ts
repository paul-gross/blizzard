import * as fs from 'node:fs';
import * as path from 'node:path';
import { type APIRequestContext, expect, mergeTests } from '@playwright/test';
import { clockEnv, RANK, type SessionHolder } from './clock-stack';
import { mockClock } from './client/clock';
import { loginThroughCli } from './login';
import { type People, type Person, peopleFactory } from './people';
import { type CliResult, CliTimeout, type Prompt, runCommand } from './cli';
import { forgeApi, type ForgeApi, type HubApi, hubApi, idpApi, mockRunnerApi } from './client/clients';
import { type Federation, federationClient } from './federation';
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
import { binDir, daemonEnv, requireBinary, TESTS_ROOT } from './env';
import { type OriginReads, originReads } from './runner-git';
import type { RunnerHost } from './runner-host';
import { type HubLog, hubLog as readHubLog } from './logs';
import { uniqueName } from './base';
import { mockWorld } from './mock-world';
import { type RunnerBinding, runnerWorld } from './runner-world';
import { type Project, sharesTenant, tenancy } from './tenancy';
import { uiWorld } from './ui';
import type { OtlpSink } from './otlp-sink';
import { mergeHostProfile, mergeProfile } from './profiles';
import { startCollector, type Collector } from './collector';
import { withSecondForge } from './second-forge';

/** A fleet-plane client for one runner, under the token minted for it. */
export interface FleetClient extends Verbs {
  runnerId: string;
  token: string;
}

interface Fixtures {
  api: HubApi;
  /** Internal: the runner fixtures' hub, the test's own tenant on the test's hub; a host needs isolation `fresh-tenant` or above. */
  runnerBinding: RunnerBinding;
  /** Repositories of the hub's forge, by name, that a host's `${forgeOrigins}` paths name; each is made before the host is built. */
  runnerOnForge: string[];
  /** The role `user` is invited as; `test.use({ userRole })`. */
  userRole: Role;
  /** One signed-in person in `tenant` with the role `userRole` names. Needs a hub with authentication on. */
  user: Person;
  /** Signs people in through the stub IdP: `create` for further invited people, `signIn` for an identity the test names. */
  people: People;
  /** Drives sign-in bounces from a browser at a runner host to the hub and back, the runner's and the hub's cookies kept apart. */
  federation: Federation;
  /** A session-free hub client; its context is independent of the signed-in admin and refused sign-in walks. */
  anonymous: HubApi;
  /** Stub IdP controls, available only on a hub owned by this test. */
  idp: ReturnType<typeof idpApi>;
  project: Project;
  /** A client sending only `Authorization: Bearer <token>`, on a context holding no cookie, so the token alone authenticates. */
  bearer: (token: string) => Promise<Verbs>;
  fleet: (name: string, options?: RunnerOptions) => Promise<FleetClient>;
  /** The hub's mock forge, and reads of its bare repositories, which `owner/name` names. */
  forge: ForgeApi & OriginReads;
  /** A separate, test-owned forge with its own bare origins. */
  secondForge: ForgeApi & OriginReads & { pid: number };
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

/** The hub's wire types, for a spec that builds a typed literal: `satisfies Wire.WorkSourceCreateRequest`. */
export type * as Wire from './client/wire/hub/types.gen';
export type { ForgeApi, HubApi };
export type { Repository };
export { FIXTURE_FORGE } from './hubs';

/** The package's `test` and `expect`, and the wire types: the only module a spec imports. */
export const test = mergeTests(tenancy, runnerWorld, uiWorld).extend<Fixtures>({
  api: async ({ testHub, tenant, level, ledger, clockMode }, use, testInfo) => {
    await use(
      hubApi({
        context: testHub.context,
        testInfo,
        tenantId: tenant.id,
        narrow: sharesTenant(level.served),
        level: level.served,
        clockMode,
        ledger,
        logContext: () => testHub.logTail(tenant.id),
        beforeRequest: () => testHub.refreshSession(),
      }),
    );
  },

  userRole: ['contributor', { option: true }],

  runnerOnForge: [[], { option: true }],

  people: async ({ testHub, tenantOf, ledger, level, playwright, clockStack }, use, testInfo) => {
    const { people, disposeAll } = peopleFactory({
      clock: clockStack,
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

  federation: async ({ testHub, playwright }, use) => {
    const { federation, disposeAll } = federationClient({ hub: testHub.coordinates, newContext: () => playwright.request.newContext() });
    await use(federation);
    await disposeAll();
  },

  anonymous: async ({ testHub, level, ledger, playwright, clockMode }, use, testInfo) => {
    const context = await playwright.request.newContext({ baseURL: testHub.coordinates.url });
    try {
      await use(hubApi({ context, testInfo, narrow: false, level: level.served, clockMode, ledger, logContext: () => testHub.logTail() }));
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

  runnerBinding: async ({ testHub, tenant, tenantOf, level, ledger, playwright, repository, runnerOnForge, clockStack }, use, testInfo) => {
    const hub = testHub.coordinates;
    // On a hub offering an IdP, `init` runs under a logged-in operator's config home; the host's own stays empty.
    // Built only then, since the `people` fixture itself refuses a hub with authentication off.
    let operator: Promise<string> | undefined;
    let disposePeople: (() => Promise<void>) | undefined;
    const operatorHome = async (): Promise<string> => {
      const made = peopleFactory({
        clock: clockStack,
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
      const holder: SessionHolder = {
        signedInAt: undefined,
        resign: async () => {
          await loginThroughCli({ run, env, person, hub });
          holder.signedInAt = await clockStack.hubReading();
        },
      };
      const registration = clockStack.register(holder);
      await loginThroughCli({ run, env, person, hub });
      holder.signedInAt = await clockStack.hubReading();
      operatorRefresh = registration.refresh;
      return home;
    };
    let operatorRefresh: (() => Promise<void>) | undefined;
    await use({
      hub: {
        url: hub.url,
        tenantId: tenant.id,
        operatorHome:
          hub.auth === undefined
            ? undefined
            : async () => {
                const home = await (operator ??= operatorHome());
                // `init` is the operator's next request: the login is made again if the clock has passed its window.
                await operatorRefresh?.();
                return home;
              },
        forgeOrigins: hub.originsPath,
      },
      admit: () => requireOwnTenant(level.served, 'runnerHost()'),
      prepare: async () => {
        for (const name of runnerOnForge) await repository(name);
      },
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
    const api = forgeApi({ context: testHub.context, testInfo, url: testHub.coordinates.forgeUrl, ledger });
    await use({ ...api, ...originReads(testHub.coordinates.originsPath, daemonEnv(), TESTS_ROOT) });
  },

  secondForge: async ({ testHub, ledger, clockStack }, use, testInfo) => {
    const dir = testInfo.outputPath('second-forge');
    await withSecondForge(
      dir,
      async (daemon) => {
        const api = forgeApi({ context: testHub.context, testInfo, url: daemon.url, ledger });
        const membership = await clockStack.join({ name: 'the second forge', rank: RANK.daemon, client: mockClock(daemon.url) });
        try {
          await use({ ...api, ...originReads(path.join(dir, 'origins'), daemonEnv(), TESTS_ROOT), pid: daemon.pid });
        } finally {
          membership.leave();
        }
      },
      clockEnv(clockStack.controlled),
    );
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

  cli: async ({ testHub, tenant, clockStack }, use, testInfo) => {
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
    const logins = new Map<string, { done: Promise<void>; refresh: () => Promise<void> }>();
    const login = async (person: Person, env: NodeJS.ProcessEnv): Promise<void> => {
      let entry = logins.get(person.userId);
      if (entry === undefined) {
        // On a controlled stack the login is made again before the next command once the clock has passed its window.
        const holder: SessionHolder = {
          signedInAt: undefined,
          resign: async () => {
            await loginThroughCli({ run, env, person, hub: testHub.coordinates });
            holder.signedInAt = await clockStack.hubReading();
          },
        };
        const registration = clockStack.register(holder);
        entry = {
          done: loginThroughCli({ run, env, person, hub: testHub.coordinates }).then(async () => {
            holder.signedInAt = await clockStack.hubReading();
          }),
          refresh: registration.refresh,
        };
        logins.set(person.userId, entry);
      }
      await entry.done;
      await entry.refresh();
    };
    await use(async (args, options = {}) => {
      const person = options.as;
      const env = envFor(testInfo.outputPath(person === undefined ? 'cli-config-home' : `cli-config-home-${person.username}`));
      if (person !== undefined) await login(person, env);
      return run(args, env);
    });
  },

  factoryContext: async ({ api, forge, project, level, testHub, ledger, clockStack }, use, testInfo) => {
    await use({ api, forge, project, level: level.served, testHub, ledger, clock: clockStack, unique: (prefix) => uniqueName(testInfo, prefix),
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

/**
 * A traced companion owns its hub and sink; both daemons export to that same sink. On a controlled stack the hub's
 * export is stepped. Its first pass only opens the export cursor at the clock's reading, so the fixture runs that pass
 * before the test can move the clock. `flushTraces` exports what the test produced and returns once the sink holds it.
 */
const traceTest = test.extend<{ traceSink: OtlpSink; traceCursor: void; flushTraces: (...hosts: RunnerHost[]) => Promise<void> }>({
  isolation: 'fresh-hub',
  traceSink: async ({ otlpSink }, use) => { await use(await otlpSink()); },
  traceCursor: [async ({ api, clockMode }, use) => {
    if (clockMode === 'controlled') await api.sweep('trace_export', { allTenants: true });
    await use();
  }, { auto: true }],
  flushTraces: async ({ api, clockMode }, use) => {
    await use(async (...hosts) => {
      if (clockMode !== 'controlled') throw new Error('flushTraces steps the export, which only a controlled stack runs stepped');
      for (const host of hosts) await host.step();
      await api.sweep('trace_export', { allTenants: true });
    });
  },
  hubEnv: async ({ hubEnv, traceSink }, use) => { await use({ ...hubEnv, OTEL_EXPORTER_OTLP_ENDPOINT: traceSink.endpoint }); },
  hubConfig: async ({ hubConfig, clockMode }, use) => { await use(mergeProfile(hubConfig, { tracing: { sweep_seconds: 1n, settle_seconds: 0n, platform: true, platform_sample_ratio: 0 }, sweeps: { trace_export: { mode: clockMode === 'controlled' ? 'stepped' : 'timed' } } })); },
  runnerConfig: async ({ runnerConfig }, use) => { await use(mergeHostProfile(runnerConfig, { tracing: { sweep_seconds: 1n, settle_seconds: 0n, platform: true, platform_sample_ratio: 0 } })); },
  runnerTraceEndpoint: async ({ traceSink }, use) => { await use(traceSink.endpoint); },
});

/** Register one execution of a scenario as the fresh-hub trace-only companion to its ordinary test. */
export function traced(title: string, body: Parameters<typeof traceTest>[2]): void {
  traceTest(`${title}_traced`, body);
}

export const collectorTest = test.extend<{ collector: Collector; collectorWait: number }>({
  collectorWait: [1, { option: true }],
  collector: async ({ collectorWait }, use, testInfo) => {
    const collector = await startCollector(testInfo.outputPath('collector'), collectorWait);
    try { await use(collector); } finally { await collector.close(); }
  },
  hubEnv: async ({ hubEnv, collector }, use) => { await use({ ...hubEnv, OTEL_EXPORTER_OTLP_ENDPOINT: collector.endpoint }); },
  hubConfig: async ({ hubConfig }, use) => { await use(mergeProfile(hubConfig, { tracing: { sweep_seconds: 1n, settle_seconds: 3n, platform: true, platform_sample_ratio: 0 }, sweeps: { trace_export: { mode: 'timed' } } })); },
  runnerConfig: async ({ runnerConfig }, use) => { await use(mergeHostProfile(runnerConfig, { tracing: { sweep_seconds: 1n, settle_seconds: 3n, platform: true, platform_sample_ratio: 0 } })); },
  runnerTraceEndpoint: async ({ collector }, use) => { await use(collector.endpoint); },
});

/** The runner world's `test`: a mock hub the test owns and runner clients, with no shared hub in reach. */
export const runnerTest = mockWorld;

/** The runner host's wire types, for a spec that builds a typed literal: `satisfies RunnerWire.FactView`. */
export type * as RunnerWire from './client/wire/runner/types.gen';
export type { TranscriptSegmentRecord } from './client/wire/hub/types.gen';
export type { OtlpSink, ReceivedItem, ReceivedSpan } from './otlp-sink';
export type { EgressCopy, EgressReader, EgressRow } from './egress';
export type { AuthStub } from './auth-stub';
export type { MockHub } from './mock-world';
export type { Bounce, Delivered, Federation } from './federation';
export type { RunnerHosts } from './runner-world';
export type { OpencodeRelease, RunnerHost, RunnerHostOptions } from './runner-host';
export { chunkProgress, holdingChunk, leaseProgress, runningLeaseWithWorker, SPAWN_HOLD, seededLeaseWithWorker, stepChunkUntil, stepUntil, stepUntilHeld } from './runner-steps';
export { BILLED_ZERO_VAR, PRICES, pricedChunk, pricedProfile } from './priced-worker';
export type { VendorUsage } from './vendor-usage';
export type { Clock } from './clock-stack';
export type { WorkerCheckpoint } from './worker-checkpoint';
export type { Viewport } from './ui';
export { expect };
export { RUNNER_VERSION } from './env';
export { packagedSystemArtifact } from './system-artifacts';
export { chunkTraceId, chunkSpanId, lifetimeTraceId, lifetimeSpanId, spanId, stepKeyText } from './trace-ids';
export type { StepKey } from './trace-ids';
export { assertChunkTrace, assertConforms, assertIdentity, assertInvocations, assertNoLeaks, assertPlatformNesting, assertRunnerNesting, assertSkeleton, isFleet, roleOf, tracesOf } from './fleet-traces';
export { migratedChunk } from './migration-world';
export type { InvocationExpect, StepExpect, StepTrace } from './fleet-traces';
export { claudeCodeExports } from './telemetry-bodies';
/** The wire shape of one reported decline reason, for a spec to type its literals against. */
export type { RunnerDeclining };
export type { MockRunner };
export type { Barrier, BarrierSubscription, Frame, Subscription } from './events';
export type { MockHubClient } from './client/mock-hub';
export type { MockChunkSpec } from './client/mock';
export type { HostRoutes, RunnerClient, RunnerHostClient, TickPass } from './client/runner';
