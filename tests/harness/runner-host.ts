import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import { parse, stringify } from 'smol-toml';
import { type CliResult, runCommand } from './cli';
import { hostSocketApi, type RunnerClient, type RunnerHostClient, runnerHostApi, type TickPass } from './client/runner';
import type { Ledger } from './client/ledger';
import { type LiveDaemon, freePort, isAlive, killLive, logTail, stopDaemon, stopLive } from './daemons/process';
import { startRunnerHost } from './daemons/runner-host';
import { binDir, daemonEnv, mockBinDir, readContract, requireBinary } from './env';
import { scanLog } from './logs';
import { checkOwnedKeys, checkProfile, expandProfile, mergeHostProfile, parseProfile, type TomlTable } from './profiles';
import type { VendorUsage } from './vendor-usage';

/** The Linux `sockaddr_un` bound the host's `host.sock` must fit, terminator excluded. */
export const SOCKET_PATH_LIMIT = 107;
/** The variable that arms the mock harness; the host's workers must carry it, and no `runnerEnv` may take it away. */
export const FENCE_VAR = 'BLIZZARD_MOCK_HARNESS_FENCE';
const FENCE_FILE = '.blizzard-mock-harness-fence';
const PLACEHOLDERS = ['workspace', 'origins', 'mockBin', 'usageBase'];
const RUNNER_BINARY = 'blizzard-runner';
const FIRST_RUNNER = 'runner-local';

/** What a test hands a host: the options of the runner-world and hub-world fixtures, one set per host. */
export interface RunnerHostOptions {
  /** Inline host-file overlay; merged over `runner.toml` beside the spec. */
  runnerConfig: TomlTable;
  /** Environment for the host process, over the isolated defaults. */
  runnerEnv: Record<string, string>;
  runnerLoop: 'stepped' | 'timed';
  /** `false` initializes the host and leaves it unstarted; `restart()` is then its first boot. */
  runnerStarted: boolean;
  /** Points the `anthropic` subscription at a minted credential and the counterpart, sampling on every host pass. */
  runnerSampling: boolean;
}

export const DEFAULT_RUNNER_OPTIONS: RunnerHostOptions = {
  runnerConfig: {},
  runnerEnv: {},
  runnerLoop: 'stepped',
  runnerStarted: true,
  runnerSampling: false,
};

/** A test-owned runner host: a real `blizzard-runner host` built only through the CLI and the documented files. */
export interface RunnerHost {
  /** The host directory. */
  readonly dir: string;
  readonly url: string;
  /** The host's Unix socket, `<dir>/host.sock`. */
  readonly socket: string;
  /** The host's private home: the config, data and credential directories its process sees. */
  readonly home: string;
  /** Its runners by name, each a client at the host's URL under the id enrollment gave. */
  readonly runners: Record<string, RunnerClient & { id: string; name: string }>;
  readonly api: RunnerHostClient;
  /** The pid of the host process now running, or last run; `undefined` before a first boot. */
  readonly pid: number | undefined;
  readonly logPath: string;
  logTail(): string;
  /** One pass of a runner, answered once it has completed; with no name, the sole runner's. */
  step(runner?: string): Promise<TickPass>;
  /** One pass of the host's own sweeps. */
  stepHost(): Promise<TickPass>;
  /** `SIGTERM` to the host's process group, `SIGKILL` after the grace. */
  stop(): Promise<number | string>;
  /** `SIGKILL` to the host's process group. */
  kill(): Promise<void>;
  /** Stops a live host, then boots the same directory on the same port. On an unstarted host, the first boot. */
  restart(): Promise<void>;
}

/** Where a host enrolls: the hub, a tenant on it, and an operator session when the hub offers an IdP. */
export interface HubBinding {
  url: string;
  tenantId?: string;
  /** A config home holding a logged-in operator's session, for `init`; the host process keeps its own. */
  operatorHome?: () => Promise<string>;
}

export interface BuilderContext {
  hub: HubBinding;
  context: APIRequestContext;
  ledger: Ledger;
  testInfo: TestInfo;
  vendor: VendorUsage;
  /** Where each host's scratch root is made; the harness check alone overrides it. */
  scratchRoot?: string;
}

export interface RunnerHostBuilder {
  create(options?: Partial<RunnerHostOptions>): Promise<RunnerHost>;
  /** Stops every host the builder made and removes their scratch roots; throws on a statement-guard refusal in a host log. */
  disposeAll(): Promise<void>;
}

/** `runnerEnv` names the contract pins, or the fence, are refused: they hold the host in its fenced world. */
export function checkRunnerEnv(runnerEnv: Record<string, string>, pinned: string[] = Object.keys(readContract().set)): string[] {
  return Object.keys(runnerEnv).flatMap((name) => {
    if (pinned.includes(name)) return [`runnerEnv ${name}: the environment contract (daemon-env.json) sets it, so a test cannot change it`];
    if (name === FENCE_VAR) return [`runnerEnv ${name}: the fixture sets it, so a test cannot change it`];
    return [];
  });
}

/** The host profile as the spec states it: `runner.toml` beside the spec, with the inline config merged over it. */
export function profileFor(specFile: string, inline: TomlTable): TomlTable {
  const file = path.join(path.dirname(specFile), 'runner.toml');
  const fromFile = fs.existsSync(file) ? parseProfile(fs.readFileSync(file, 'utf8')) : {};
  return mergeHostProfile(fromFile, inline);
}

/** Every problem in a host profile and its env; nothing has started when this is asked. */
export function checkHostOptions(profile: TomlTable, runnerEnv: Record<string, string>): string[] {
  return [...checkProfile(profile, { contract: 'host', placeholders: PLACEHOLDERS }), ...checkOwnedKeys(profile), ...checkRunnerEnv(runnerEnv)];
}

/** The refusal for a socket path over the limit, naming the path and its length; `undefined` when it fits. */
export function socketPathProblem(socket: string): string | undefined {
  const length = Buffer.byteLength(socket);
  return length <= SOCKET_PATH_LIMIT
    ? undefined
    : `the host socket ${socket} is ${length} bytes, over the ${SOCKET_PATH_LIMIT}-byte limit a Unix socket path takes; use a shorter scratch root`;
}

function readHostFile(dir: string): TomlTable {
  return parse(fs.readFileSync(path.join(dir, 'blizzard-host.toml'), 'utf8')) as TomlTable;
}

function runnerEntries(file: TomlTable): { name: string; id: string }[] {
  return ((file.runner ?? []) as TomlTable[]).map((entry) => ({ name: entry.name as string, id: entry.id as string }));
}

interface Timings {
  mintMs: number;
  initMs: number;
  addMs: number;
  bootMs: number;
}

export function runnerHostBuilder(ctx: BuilderContext): RunnerHostBuilder {
  const { testInfo } = ctx;
  const made: { dispose: (failed: boolean) => Promise<string[]> }[] = [];
  let count = 0;

  const create = async (given: Partial<RunnerHostOptions> = {}): Promise<RunnerHost> => {
    const options: RunnerHostOptions = { ...DEFAULT_RUNNER_OPTIONS, ...given };
    const profile = profileFor(testInfo.file, options.runnerConfig);
    const problems = checkHostOptions(profile, options.runnerEnv);
    if (problems.length > 0) throw new Error(`the runner host options are unsound:\n${problems.join('\n')}`);

    const index = count++;
    // A hub holds one live host per name and a host is named for its first runner, so a test's later hosts each name theirs.
    const defaultRunner = index === 0 ? FIRST_RUNNER : `${FIRST_RUNNER}-${index}`;
    const root = path.join(ctx.scratchRoot ?? os.tmpdir(), `bzh-${crypto.randomBytes(4).toString('hex')}`);
    const dir = path.join(root, 'h');
    const socket = path.join(dir, 'host.sock');
    const tooLong = socketPathProblem(socket);
    if (tooLong !== undefined) throw new Error(tooLong);

    const home = path.join(root, 'home');
    const logPath = testInfo.outputPath(`runner-host-${index}.log`);
    const timings: Timings = { mintMs: 0, initMs: 0, addMs: 0, bootMs: 0 };
    let daemon: LiveDaemon | undefined;
    let disposed = false;

    const dispose = async (failed: boolean): Promise<string[]> => {
      if (disposed) return [];
      disposed = true;
      if (daemon !== undefined) await stopDaemon(daemon);
      const refusals = scanLog(logPath).map((line) => line.raw);
      if (failed && fs.existsSync(logPath)) await testInfo.attach(`runner host ${index} log`, { body: logTail(logPath), contentType: 'text/plain' });
      await testInfo.attach(`runner host ${index} timings`, { body: JSON.stringify(timings), contentType: 'application/json' });
      fs.rmSync(root, { recursive: true, force: true });
      return refusals;
    };
    made.push({ dispose });

    try {
      fs.mkdirSync(home, { recursive: true });
      const env = isolatedEnv(home, path.join(home, '.config'), options.runnerEnv);
      const cliEnv = (configHome: string): NodeJS.ProcessEnv => ({ ...env, XDG_CONFIG_HOME: configHome });
      const run = async (command: string, args: string[], cliHome: string): Promise<CliResult> => {
        const result = await runCommand(command, args, { env: cliEnv(cliHome), cwd: root });
        if (result.code !== 0) throw new Error(`\`${[path.basename(command), ...args].join(' ')}\` exited ${result.code}:\n${result.stdout}${result.stderr}`);
        return result;
      };

      // The world: bare origins and a workspace, with the winter source unset so the fixture is winter-free.
      const mock = requireBinary(mockBinDir(), 'blizzard-mock-fixture');
      const scratch = path.join(root, 'world');
      let started = Date.now();
      await run(mock, ['mint', '--env', 'w', '--scratch-root', scratch], home);
      const workspace = (await run(mock, ['path', '--env', 'w', '--scratch-root', scratch], home)).stdout.trim();
      const origins = (await run(mock, ['path', '--env', 'w', '--scratch-root', scratch, '--part', 'origins'], home)).stdout.trim();
      fs.writeFileSync(path.join(workspace, FENCE_FILE), '');
      timings.mintMs = Date.now() - started;

      fs.writeFileSync(path.join(home, '.gitconfig'), '[user]\n\tname = harness\n\temail = harness@example.invalid\n');
      writeClaudeCredential(home);

      const runner = requireBinary(binDir(), RUNNER_BINARY);
      const operatorHome = ctx.hub.operatorHome === undefined ? path.join(home, '.config') : await ctx.hub.operatorHome();
      started = Date.now();
      if (index > 0) {
        // `init` scaffolds the file before it reaches the hub, so a first pass against nothing leaves the scaffold to
        // rename; the second pass registers the host under that name.
        await runCommand(runner, ['init', dir, '--hub', 'http://127.0.0.1:9'], { env: cliEnv(operatorHome), cwd: root });
        if (!fs.existsSync(path.join(dir, 'blizzard-host.toml'))) {
          throw new Error('`blizzard-runner init` left no host file when the hub was unreachable, so the second host cannot name its runner; the scaffold-then-rename step needs revisiting');
        }
        const scaffold = readHostFile(dir);
        scaffold.hub_url = ctx.hub.url;
        (scaffold.runner as TomlTable[])[0]!.name = defaultRunner;
        fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(scaffold));
      }
      const init = await run(runner, ['init', dir, '--hub', ctx.hub.url, ...(ctx.hub.tenantId === undefined ? [] : ['--tenant', ctx.hub.tenantId])], operatorHome);
      timings.initMs = Date.now() - started;
      const hostId = /\((hs_[0-9A-Z]+)\)/.exec(init.stdout + init.stderr)?.[1];
      if (hostId !== undefined) ctx.ledger.add('host', hostId);

      started = Date.now();
      for (const entry of (profile.runner ?? []) as TomlTable[]) {
        const name = entry.name as string;
        if (name === defaultRunner) continue;
        const projects = ((entry.projects ?? []) as TomlTable[]).flatMap((item) => [
          '--project', item.project as string,
          ...(item.workspace === undefined ? [] : ['--workspace', item.workspace as string]),
        ]);
        const agents = entry.agents === undefined ? [] : ['--agents', String(entry.agents)];
        await run(runner, ['add', name, '--dir', dir, ...(projects.length > 0 ? projects : ['--project', 'default']), ...agents], home);
      }
      timings.addMs = Date.now() - started;

      // The overlay: the fixture's baseline first, then the profile, over the file the CLI wrote.
      let port = await freePort();
      const written = readHostFile(dir);
      const baseline: TomlTable = {
        host: '127.0.0.1',
        port,
        workspace: [
          {
            id: 'workspace-local',
            root: workspace,
            provider: 'basic',
            base_branch: 'main',
            repo: fs
              .readdirSync(origins)
              .filter((name) => name.endsWith('.git'))
              .sort()
              .map((name) => ({ name: name.slice(0, -'.git'.length), url: `file://${path.join(origins, name)}` })),
          },
        ],
        subscription: [
          options.runnerSampling
            ? { slug: 'anthropic', usage_api_base: ctx.vendor.url, credentials_path: ctx.vendor.mintAnthropic(path.join(root, 'vendor')).path, sample_interval_seconds: 0 }
            : { slug: 'anthropic', usage_api_base: ctx.vendor.url, credentials_path: path.join(root, 'vendor', 'absent.json') },
        ],
        runner: runnerEntries(written).map((entry) => ({
          name: entry.name,
          transcripts_root: path.join(root, 'transcripts'),
          claude_code: { binary: path.join(mockBinDir(), 'mock-claude-code') },
          opencode: { enabled: false },
          worker: { env_passthrough: [FENCE_VAR, 'BZ_TRANSCRIPTS_ROOT'] },
        })),
      };
      const expanded = expandProfile(profile, { workspace, origins, mockBin: mockBinDir(), usageBase: ctx.vendor.url });
      const merged = mergeHostProfile(mergeHostProfile(written, baseline), expanded);
      fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(merged));

      const entries = runnerEntries(readHostFile(dir));
      for (const entry of entries) ctx.ledger.add('runner', entry.id);

      const boot = async (): Promise<void> => {
        const begun = Date.now();
        daemon = await startRunnerHost({ binary: runner, dir, port, loop: options.runnerLoop, logPath, env });
        timings.bootMs = Date.now() - begun;
      };
      if (options.runnerStarted) {
        try {
          await boot();
        } catch (error) {
          // The port was free when chosen; another process may have taken it before the first bind. Nothing has been
          // handed to the test yet, so the host file can name another.
          if (!/could not take port/.test(String(error))) throw error;
          port = await freePort();
          const file = readHostFile(dir);
          file.port = port;
          fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(file));
          await boot();
        }
      }
      const url = `http://127.0.0.1:${port}`;

      const api = runnerHostApi({ context: ctx.context, testInfo, url, ledger: ctx.ledger, logContext: () => logTail(logPath) });
      const sock = hostSocketApi({ socket, logContext: () => logTail(logPath) });
      const runners: RunnerHost['runners'] = {};
      for (const entry of entries) runners[entry.name] = { ...api.runner(entry.id), id: entry.id, name: entry.name };

      return {
        dir,
        url,
        socket,
        home,
        runners,
        api,
        get pid() {
          return daemon?.pid;
        },
        logPath,
        logTail: () => logTail(logPath),
        step(name) {
          const names = Object.keys(runners);
          const target = name ?? (names.length === 1 ? names[0] : undefined);
          if (target === undefined) throw new Error(`step() needs a runner name: the host has ${names.length} runners (${names.join(', ')})`);
          const known = runners[target];
          if (known === undefined) throw new Error(`the host has no runner ${target} (it has ${names.join(', ')})`);
          return sock.stepRunner(known.id);
        },
        stepHost: () => sock.stepHost(),
        async stop() {
          if (daemon === undefined) throw new Error('the host was never started; restart() boots it');
          return stopLive(daemon);
        },
        async kill() {
          if (daemon === undefined) throw new Error('the host was never started; restart() boots it');
          await killLive(daemon);
        },
        async restart() {
          if (daemon !== undefined && isAlive(daemon.pid)) await stopLive(daemon);
          await boot();
        },
      };
    } catch (error) {
      await dispose(true);
      throw error;
    }
  };

  return {
    create,
    async disposeAll() {
      const failed = testInfo.status !== testInfo.expectedStatus;
      const refusals: string[] = [];
      for (const host of made.splice(0).reverse()) refusals.push(...(await host.dispose(failed)));
      if (refusals.length > 0) throw new Error(`a statement guard refused in a runner host:\n${refusals.join('\n')}`);
    },
  };
}

/** The environment of a host and of every CLI that builds it: the developer's credentials and scaffold seeds left out. */
export function isolatedEnv(home: string, configHome: string, runnerEnv: Record<string, string>): NodeJS.ProcessEnv {
  const pinned = Object.keys(readContract().set);
  const env = daemonEnv(
    {
      HOME: home,
      XDG_DATA_HOME: path.join(home, '.local', 'share'),
      XDG_CACHE_HOME: path.join(home, '.cache'),
      CLAUDE_CONFIG_DIR: path.join(home, '.claude'),
      CODEX_HOME: path.join(home, '.codex'),
    },
    configHome,
  );
  for (const name of Object.keys(env)) {
    // Every `BZ_*` the host reads is a seed or a credential; the pinned ones stay, and `runnerEnv` names the rest.
    if (name.startsWith('BZ_') && !pinned.includes(name)) delete env[name];
  }
  delete env.BLIZZARD_MOCK_WINTER_SOURCE;
  Object.assign(env, runnerEnv);
  return { ...env, [FENCE_VAR]: '1' };
}

/** The Claude credential the harness-health probe reads at the private home's default path: synthetic, and far from expiry. */
function writeClaudeCredential(home: string): void {
  const file = path.join(home, '.claude', '.credentials.json');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ claudeAiOauth: { accessToken: 'synthetic-claude-login', refreshToken: 'synthetic', expiresAt: Date.UTC(2099, 0, 1) } }), { mode: 0o600 });
}
