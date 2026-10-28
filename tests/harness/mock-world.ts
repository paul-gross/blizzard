import * as fs from 'node:fs';
import * as path from 'node:path';
import type { APIRequestContext } from '@playwright/test';
import { type AuthStub, startAuthStub } from './auth-stub';
import { base, uniqueName } from './base';
import { type MockHubClient, mockHubApi } from './client/mock-hub';
import { type RunnerHostClient, runnerHostApi } from './client/runner';
import { startMockHub } from './daemons/mock-hub';
import { isAlive, killLive, type LiveDaemon, logTail, stopDaemon, stopLive } from './daemons/process';
import { runDir } from './env';
import { type Barrier, type BarrierSubscription, openRunnerStream, type Subscription } from './events';
import { scanLog } from './logs';
import { DEFAULT_RUNNER_OPTIONS, type RunnerHost, type RunnerHostOptions, runnerHostBuilder } from './runner-host';
import { startVendorUsage, type VendorUsage } from './vendor-usage';

/** A test's own mock hub: its process, stopped, killed, and started again under the URL it first held, and its clients. */
export interface MockHub extends MockHubClient {
  /** The pid of the mock hub process now running, or last run. */
  readonly pid: number;
  /** `SIGTERM`, then `SIGKILL` after the grace; resolves with the exit code, or the signal that ended it. */
  stop(): Promise<number | string>;
  /** `SIGKILL`; resolves once the process is gone. */
  kill(): Promise<void>;
  /** Hosts the stopped mock hub again on the same URL. Its in-memory state is gone, as a restarted mock's would be. */
  start(): Promise<void>;
  /** The file the mock hub's stdout and stderr are appended to, across restarts. */
  readonly logPath: string;
  /** The tail of the mock hub's log, as a failing test has it attached. */
  logTail(): string;
}

/** The options a runner host is built with; `test.use({ … })` sets them for a file or a describe, `runnerHosts.create` per host. */
export interface RunnerHostFixtureOptions {
  runnerConfig: RunnerHostOptions['runnerConfig'];
  runnerEnv: RunnerHostOptions['runnerEnv'];
  runnerLoop: RunnerHostOptions['runnerLoop'];
  runnerStarted: RunnerHostOptions['runnerStarted'];
  runnerSampling: RunnerHostOptions['runnerSampling'];
}

/** Makes further runner hosts beside the test's own, each with options of its own over the test's. */
export interface RunnerHosts {
  create(options?: Partial<RunnerHostOptions>): Promise<RunnerHost>;
}

interface MockWorldFixtures extends RunnerHostFixtureOptions {
  mockHub: MockHub;
  /** A loopback stand-in for the vendors' usage endpoints, closed when the test ends. */
  vendorUsage: VendorUsage;
  /** The test's runner host: a real `blizzard-runner host` enrolled at `mockHub`, built through the CLI and the documented files. */
  runnerHost: RunnerHost;
  /** Further runner hosts, each its own directory, port and runners. */
  runnerHosts: RunnerHosts;
  /** Internal: the builder every runner host of the test comes from, stopped when the test ends. */
  runnerHostBuilder: ReturnType<typeof runnerHostBuilder>;
  /** A loopback hub-auth stub with the key set the test gives it, closed when the test ends. */
  authStub: (keys?: unknown[]) => Promise<AuthStub>;
  /** A client for a runner host's local API at `url`; `logContext` names the host log a failure attaches. */
  runnerHostClient: (url: string, options?: { logContext?: () => string; headers?: Record<string, string> }) => RunnerHostClient;
  /** A runner host's event stream, closed when the test ends. */
  runnerStream: (
    hostUrl: string,
    options?: { headers?: Record<string, string>; fromNow?: boolean; reconnect?: boolean; barrier?: Barrier },
  ) => Promise<BarrierSubscription>;
  /** Internal: the request context the world's clients share. */
  worldContext: APIRequestContext;
  /** Internal: the stream subscriptions a test opened, closed when it ends. */
  openedStreams: Subscription[];
}

/**
 * The runner world: a mock hub the test alone owns, and typed clients for it and for a runner host. It rests on `base`
 * and imports nothing from the hub world, so a runner-only test reaches no shared hub, no shared reset, and no shared
 * forge; its levers, seeds, captures and tokens are global only to its own mock hub's process.
 */
export const mockWorld = base.extend<MockWorldFixtures>({
  worldContext: async ({ playwright }, use) => {
    const context = await playwright.request.newContext();
    await use(context);
    await context.dispose();
  },

  mockHub: async ({ worldContext, ledger }, use, testInfo) => {
    const dir = path.join(runDir(), 'mock-hubs', uniqueName(testInfo));
    fs.mkdirSync(dir, { recursive: true });
    let daemon: LiveDaemon = await startMockHub(dir);
    const { url, logPath } = daemon;
    const tail = (): string => logTail(logPath);
    const clients = mockHubApi({ context: worldContext, testInfo, url, ledger, logContext: tail });
    try {
      await use({
        ...clients,
        get pid() {
          return daemon.pid;
        },
        stop: () => stopLive(daemon),
        kill: async () => {
          await killLive(daemon);
        },
        start: async () => {
          if (isAlive(daemon.pid)) throw new Error('the mock hub is running; stop() or kill() it before start()');
          daemon = await startMockHub(dir, { port: Number(new URL(url).port) });
        },
        logPath,
        logTail: tail,
      });
    } finally {
      // The process now, not the one the fixture started: a `start()` replaced it.
      await stopDaemon(daemon);
      if (testInfo.status !== testInfo.expectedStatus) await testInfo.attach('mock hub log', { body: tail(), contentType: 'text/plain' });
    }
    // The whole log is read for refusals the moment the mock hub stops.
    const refusals = scanLog(logPath).map((line) => line.raw);
    if (refusals.length > 0) throw new Error(`a statement guard refused in the mock hub:\n${refusals.join('\n')}`);
  },

  authStub: async ({}, use) => {
    const started: AuthStub[] = [];
    await use(async (keys) => {
      const stub = await startAuthStub(keys);
      started.push(stub);
      return stub;
    });
    await Promise.all(started.map((stub) => stub.close()));
  },

  runnerConfig: [DEFAULT_RUNNER_OPTIONS.runnerConfig, { option: true }],
  runnerEnv: [DEFAULT_RUNNER_OPTIONS.runnerEnv, { option: true }],
  runnerLoop: [DEFAULT_RUNNER_OPTIONS.runnerLoop, { option: true }],
  runnerStarted: [DEFAULT_RUNNER_OPTIONS.runnerStarted, { option: true }],
  runnerSampling: [DEFAULT_RUNNER_OPTIONS.runnerSampling, { option: true }],

  vendorUsage: async ({}, use) => {
    const vendor = await startVendorUsage();
    await use(vendor);
    await vendor.close();
  },

  runnerHostBuilder: async ({ mockHub, worldContext, ledger, vendorUsage }, use, testInfo) => {
    const builder = runnerHostBuilder({ hub: { url: mockHub.url }, context: worldContext, ledger, testInfo, vendor: vendorUsage });
    await use(builder);
    await builder.disposeAll();
  },

  runnerHosts: async ({ runnerHostBuilder: builder, runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling }, use) => {
    const defaults = { runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling };
    await use({ create: (options = {}) => builder.create({ ...defaults, ...options }) });
  },

  runnerHost: async ({ runnerHosts }, use) => {
    await use(await runnerHosts.create());
  },

  runnerHostClient: async ({ worldContext, ledger }, use, testInfo) => {
    await use((url, options = {}) =>
      runnerHostApi({ context: worldContext, testInfo, url, ledger, logContext: options.logContext ?? (() => ''), headers: options.headers }),
    );
  },

  openedStreams: async ({}, use) => {
    const opened: Subscription[] = [];
    await use(opened);
    for (const subscription of opened) subscription.close();
  },

  runnerStream: async ({ openedStreams }, use) => {
    await use((hostUrl, options = {}) => openRunnerStream({ hostUrl, ...options, opened: openedStreams }));
  },
});
