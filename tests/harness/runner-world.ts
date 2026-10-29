import type { APIRequestContext } from '@playwright/test';
import { base } from './base';
import { Ledger } from './client/ledger';
import { type RunnerHostClient, runnerHostApi } from './client/runner';
import { type Barrier, type BarrierSubscription, openRunnerStream, type Subscription } from './events';
import { DEFAULT_RUNNER_OPTIONS, type HubBinding, type RunnerHost, type RunnerHostOptions, runnerHostBuilder } from './runner-host';
import { type ScratchDirs, scratchDirs, startPreHostDaemon } from './scratch-dirs';
import { startVendorUsage, type VendorUsage } from './vendor-usage';
import { startWorkerCheckpoint, type WorkerCheckpoint } from './worker-checkpoint';

/** The options a runner host is built with; `test.use({ … })` sets them for a file or a describe, `runnerHosts.create` per host. */
export interface RunnerHostFixtureOptions {
  runnerConfig: RunnerHostOptions['runnerConfig'];
  runnerEnv: RunnerHostOptions['runnerEnv'];
  runnerLoop: RunnerHostOptions['runnerLoop'];
  runnerStarted: RunnerHostOptions['runnerStarted'];
  runnerSampling: RunnerHostOptions['runnerSampling'];
  runnerSubscribed: RunnerHostOptions['runnerSubscribed'];
  runnerOtlp: RunnerHostOptions['runnerOtlp'];
  runnerTraceEndpoint: RunnerHostOptions['runnerTraceEndpoint'];
  runnerBundle: RunnerHostOptions['runnerBundle'];
  runnerStubs: RunnerHostOptions['runnerStubs'];
  runnerWinterTrap: RunnerHostOptions['runnerWinterTrap'];
  runnerDir: RunnerHostOptions['runnerDir'];
  runnerRepositories: RunnerHostOptions['runnerRepositories'];
}

/** Makes further runner hosts beside the test's own, each with options of its own over the test's. */
export interface RunnerHosts {
  create(options?: Partial<RunnerHostOptions>): Promise<RunnerHost>;
}

/** What a world gives the runner fixtures: the hub its hosts enroll at, and a refusal for a host the world cannot admit. */
export interface RunnerBinding {
  hub: HubBinding;
  /** Throws when the test may not make a host; asked before each. */
  admit?(): void;
  /** Readies the world for a host, once before each is built: whatever the host's configuration names that must exist first. */
  prepare?(): Promise<void>;
}

interface RunnerWorldFixtures extends RunnerHostFixtureOptions {
  /** Internal: the plain request context the world's clients share. */
  worldContext: APIRequestContext;
  /** Internal: where this world's hosts enroll. Each world supplies it; the runner fixtures never choose a hub. */
  runnerBinding: RunnerBinding;
  /** A loopback stand-in for the vendors' usage endpoints, closed when the test ends. */
  vendorUsage: VendorUsage;
  /** A place a mock worker stops until the test releases it, closed (and everything held released) when the test ends. */
  workerCheckpoint: WorkerCheckpoint;
  /** The test's runner host: a real `blizzard-runner host` built through the CLI and the documented files. */
  runnerHost: RunnerHost;
  /** Further runner hosts, each its own directory, port and runners. */
  runnerHosts: RunnerHosts;
  /** Internal: the builder every runner host of the test comes from, stopped when the test ends. */
  runnerHostBuilder: ReturnType<typeof runnerHostBuilder>;
  /** Directories no host serves, for `blizzard-runner` verbs to run on; no mock hub is started for them. Removed when the test ends. */
  scratchDirs: ScratchDirs;
  /** A pre-host daemon still serving `dir`: a listener on its `runner.sock` answering every request `200 {}`; yields the socket. Closed when the test ends. */
  preHostDaemon: (dir: string) => Promise<string>;
  /** A client for a runner host's local API at `url`; `logContext` names the host log a failure attaches. */
  runnerHostClient: (url: string, options?: { logContext?: () => string; headers?: Record<string, string> }) => RunnerHostClient;
  /** A runner host's event stream, closed when the test ends. */
  runnerStream: (
    hostUrl: string,
    options?: { headers?: Record<string, string>; fromNow?: boolean; reconnect?: boolean; barrier?: Barrier },
  ) => Promise<BarrierSubscription>;
  /** Internal: the stream subscriptions a test opened, closed when it ends. */
  openedStreams: Subscription[];
}

/**
 * The runner side of both worlds, defined once: the host builder, its options, the host and stream clients, and their
 * teardown. It rests on `base` and imports nothing from the hub world; each world extends it with the one fixture it
 * owns, `runnerBinding`, naming the hub a host enrolls at.
 */
export const runnerWorld = base.extend<RunnerWorldFixtures>({
  worldContext: async ({ playwright }, use) => {
    const context = await playwright.request.newContext();
    await use(context);
    await context.dispose();
  },

  runnerBinding: async ({}, use) => {
    await use({
      hub: { url: '' },
      admit: () => {
        throw new Error('this world binds its runner hosts to no hub');
      },
    });
  },

  runnerConfig: [DEFAULT_RUNNER_OPTIONS.runnerConfig, { option: true }],
  runnerEnv: [DEFAULT_RUNNER_OPTIONS.runnerEnv, { option: true }],
  runnerLoop: [DEFAULT_RUNNER_OPTIONS.runnerLoop, { option: true }],
  runnerStarted: [DEFAULT_RUNNER_OPTIONS.runnerStarted, { option: true }],
  runnerSampling: [DEFAULT_RUNNER_OPTIONS.runnerSampling, { option: true }],
  runnerSubscribed: [DEFAULT_RUNNER_OPTIONS.runnerSubscribed, { option: true }],
  runnerOtlp: [DEFAULT_RUNNER_OPTIONS.runnerOtlp, { option: true }],
  runnerTraceEndpoint: [DEFAULT_RUNNER_OPTIONS.runnerTraceEndpoint, { option: true }],
  runnerBundle: [DEFAULT_RUNNER_OPTIONS.runnerBundle, { option: true }],
  runnerStubs: [DEFAULT_RUNNER_OPTIONS.runnerStubs, { option: true }],
  runnerWinterTrap: [DEFAULT_RUNNER_OPTIONS.runnerWinterTrap, { option: true }],
  runnerDir: [DEFAULT_RUNNER_OPTIONS.runnerDir, { option: true }],
  runnerRepositories: [DEFAULT_RUNNER_OPTIONS.runnerRepositories, { option: true }],

  vendorUsage: async ({}, use) => {
    const vendor = await startVendorUsage();
    await use(vendor);
    await vendor.close();
  },

  workerCheckpoint: async ({}, use) => {
    const checkpoint = await startWorkerCheckpoint();
    await use(checkpoint);
    await checkpoint.close();
  },

  runnerHostBuilder: async ({ runnerBinding, worldContext, ledger, vendorUsage }, use, testInfo) => {
    const builder = runnerHostBuilder({ hub: runnerBinding.hub, context: worldContext, ledger, testInfo, vendor: vendorUsage });
    await use(builder);
    await builder.disposeAll();
  },

  runnerHosts: async (
    { runnerBinding, runnerHostBuilder: builder, runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling, runnerSubscribed, runnerOtlp, runnerTraceEndpoint, runnerBundle, runnerStubs, runnerWinterTrap, runnerDir, runnerRepositories },
    use,
  ) => {
    const defaults = { runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling, runnerSubscribed, runnerOtlp, runnerTraceEndpoint, runnerBundle, runnerStubs, runnerWinterTrap, runnerDir, runnerRepositories };
    await use({
      create: async (options = {}) => {
        runnerBinding.admit?.();
        await runnerBinding.prepare?.();
        return builder.create({ ...defaults, ...options });
      },
    });
  },

  runnerHost: async (
    { runnerHosts, runnerBinding, held, heldStacks, playwright, runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling, runnerSubscribed, runnerOtlp, runnerTraceEndpoint, runnerBundle, runnerStubs, runnerWinterTrap, runnerDir },
    use,
    testInfo,
  ) => {
    if (held === undefined) {
      await use(await runnerHosts.create());
      return;
    }
    // A held block's host is the worker's: built by the first test, with a context, usage stand-in and ledger that outlive it.
    const options = { runnerConfig, runnerEnv, runnerLoop, runnerStarted, runnerSampling, runnerSubscribed, runnerOtlp, runnerTraceEndpoint, runnerBundle, runnerStubs, runnerWinterTrap, runnerDir };
    await use(
      await heldStacks.acquire(`host:${held}`, async () => {
        runnerBinding.admit?.();
        await runnerBinding.prepare?.();
        const context = await playwright.request.newContext();
        const vendor = await startVendorUsage();
        const builder = runnerHostBuilder({ hub: runnerBinding.hub, context, ledger: new Ledger(), testInfo, vendor });
        try {
          const host = await builder.create(options);
          return {
            value: host,
            dispose: async () => {
              try {
                await builder.disposeAll();
              } finally {
                await vendor.close();
                await context.dispose();
              }
            },
          };
        } catch (error) {
          await builder.disposeAll();
          await vendor.close();
          await context.dispose();
          throw error;
        }
      }),
    );
  },

  scratchDirs: async ({}, use) => {
    const dirs = scratchDirs();
    await use(dirs);
    dirs.dispose();
  },

  preHostDaemon: async ({}, use) => {
    const open: { close(): Promise<void> }[] = [];
    await use(async (dir) => {
      const daemon = await startPreHostDaemon(dir);
      open.push(daemon);
      return daemon.socket;
    });
    for (const daemon of open) await daemon.close();
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
