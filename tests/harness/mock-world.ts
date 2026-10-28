import * as fs from 'node:fs';
import * as path from 'node:path';
import { type AuthStub, startAuthStub } from './auth-stub';
import { uniqueName } from './base';
import { type MockHubClient, mockHubApi } from './client/mock-hub';
import { startMockHub } from './daemons/mock-hub';
import { isAlive, killLive, type LiveDaemon, logTail, stopDaemon, stopLive } from './daemons/process';
import { runDir } from './env';
import { scanLog } from './logs';
import { type RunnerBinding, runnerWorld } from './runner-world';

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

interface MockWorldFixtures {
  mockHub: MockHub;
  /** A loopback hub-auth stub with the key set the test gives it, closed when the test ends. */
  authStub: (keys?: unknown[]) => Promise<AuthStub>;
  /** Internal: the runner fixtures' hub, the test's own mock hub. */
  runnerBinding: RunnerBinding;
}

/**
 * The runner world: a mock hub the test alone owns, and typed clients for it and for a runner host. It rests on `base`
 * and imports nothing from the hub world, so a runner-only test reaches no shared hub, no shared reset, and no shared
 * forge; its levers, seeds, captures and tokens are global only to its own mock hub's process.
 */
export const mockWorld = runnerWorld.extend<MockWorldFixtures>({
  runnerBinding: async ({ mockHub }, use) => {
    await use({ hub: { url: mockHub.url } });
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
});
