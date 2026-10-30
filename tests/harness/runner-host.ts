import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import { parse, stringify } from 'smol-toml';
import { type CliResult, runCommand } from './cli';
import { type HostSocketClient, hostSocketApi, type RunnerClient, type RunnerHostClient, runnerHostApi, type TickPass } from './client/runner';
import type { Ledger } from './client/ledger';
import { type LiveDaemon, freePort, isAlive, killLive, logTail, stopDaemon, stopLive } from './daemons/process';
import { startRunnerHost } from './daemons/runner-host';
import { BLIZZARD_ROOT, binDir, daemonEnv, mockBinDir, readContract, requireBinary } from './env';
import { scanLog } from './logs';
import { checkOwnedKeys, checkProfile, expandProfile, mergeHostProfile, parseProfile, type TomlTable } from './profiles';
import { type OtlpSink, startOtlpSink } from './otlp-sink';
import { type OriginReads, originReads } from './runner-git';
import type { VendorUsage } from './vendor-usage';

/** The Linux `sockaddr_un` bound the host's `host.sock` must fit, terminator excluded. */
export const SOCKET_PATH_LIMIT = 107;
/** The variable that arms the mock harness; the host's workers must carry it, and no `runnerEnv` may take it away. */
export const FENCE_VAR = 'BLIZZARD_MOCK_HARNESS_FENCE';
const FENCE_FILE = '.blizzard-mock-harness-fence';
const PLACEHOLDERS = ['workspace', 'origins', 'mockBin', 'usageBase', 'bundle', 'stubs'];
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
  /** `false` declares no subscription at all, so the host's runner names none and its panel lists none. */
  runnerSubscribed: boolean;
  /**
   * Starts a sink the host exports its traces to: the host's `OTEL_EXPORTER_OTLP_ENDPOINT` names it, and it is the
   * handle's `otlp`. Whether the host traces at all stays a `runner.toml` or `runnerConfig` setting. `'hung'` starts a
   * sink that accepts each connection and never answers.
   */
  runnerOtlp: boolean | 'hung';
  /** Export to a sink owned by the test rather than creating a host-owned sink. */
  runnerTraceEndpoint: string | undefined;
  /**
   * Files of a harness bundle, by path under the bundle directory (`opencode/opencode.json`), written into a test-owned
   * directory before the host boots. A profile names the directory as `${bundle}`, as in `harness.config_dir`.
   */
  runnerBundle: Record<string, string>;
  /**
   * Executable scripts by file name, written into a test-owned directory before the host boots; a profile names the
   * directory as `${stubs}`, as in `runner[].worker.path_prepend`. A script, and a `runnerBundle` file, names the
   * host's log directory as `${logs}`; `workerLog` reads what was appended there.
   */
  runnerStubs: Record<string, string>;
  /**
   * Prices OpenCode models for the host: per-token rates by `provider` then `model`, written as the `opencode/models.json`
   * of a test-owned cache home the host's `XDG_CACHE_HOME` names. A `runnerEnv` naming `XDG_CACHE_HOME` wins.
   */
  runnerModelPrices: Record<string, Record<string, { input: number; output: number; cache_read: number; cache_write: number }>>;
  /**
   * Puts a `winter` that records its invocation and exits 99 first on the host's `PATH`, over the ambient one, so a host
   * that reaches for winter is caught; the handle's `winterInvoked()` reports it. A `runnerEnv.PATH` beside it is refused.
   */
  runnerWinterTrap: boolean;
  /**
   * Lays the host out as an adopted runner: the runner `name` the operator already minted at the hub (`runnerId` and
   * its `token`) on a host scaffolded but never registered, so the hub lists no host for it. The ids and token go
   * into the documented host files through `prepareStopped`. Needs `runnerStarted: false`: the host's first boot is
   * the runner's own registration.
   */
  runnerAdopted: { name: string; runnerId: string; token: string } | undefined;
}

export const DEFAULT_RUNNER_OPTIONS: RunnerHostOptions = {
  runnerConfig: {},
  runnerEnv: {},
  runnerLoop: 'stepped',
  runnerStarted: true,
  runnerSampling: false,
  runnerSubscribed: true,
  runnerOtlp: false,
  runnerTraceEndpoint: undefined,
  runnerBundle: {},
  runnerStubs: {},
  runnerModelPrices: {},
  runnerWinterTrap: false,
  runnerAdopted: undefined,
};

/** A test-owned runner host: a real `blizzard-runner host` built only through the CLI and the documented files. */
export interface RunnerHost extends OriginReads {
  /** The host directory. */
  readonly dir: string;
  /** Minted fixture workspace; its basic provider creates one environment named for each chunk. */
  readonly workspace: string;
  readonly url: string;
  /** The host's Unix socket, `<dir>/host.sock`. */
  readonly socket: string;
  /** The host's private home: the config, data and credential directories its process sees. */
  readonly home: string;
  /** Its runners by name, each a client at the host's URL under the id enrollment gave. */
  readonly runners: Record<string, RunnerClient & { id: string; name: string }>;
  readonly api: RunnerHostClient;
  /** The same host routes over `host.sock`, with the step routes. */
  readonly socketApi: HostSocketClient;
  /** The pid of the host process now running, or last run; `undefined` before a first boot. */
  readonly pid: number | undefined;
  readonly logPath: string;
  /** The sink the host exports to, when `runnerOtlp` asked for one; it is closed when the host is disposed. */
  readonly otlp: OtlpSink | undefined;
  /**
   * Where the host reads its workers' transcripts: the default runner's `transcripts_root`, which the host process
   * carries as `BZ_TRANSCRIPTS_ROOT` for the workers it spawns. Read from the host file at each boot.
   */
  readonly transcriptsRoot: string;
  logTail(): string;
  /** The whole host log, across restarts. */
  log(): string;
  /** The hub tokens enrollment issued, read now from the host's `.env` by the names the host contract declares. */
  hubTokens(): HostHubTokens;
  /** Empties the stopped host's `.env`, as a host no hub added holds no token; the next boot reads none. Throws when the host is running. */
  clearHubTokens(): void;
  /**
   * The operator's rename on the stopped host: the runner `from` is named `to` in the host file, its token moves to the
   * variable the host contract derives for `to`, and its `runners/<name>` directory, when there is one, follows. The
   * handle's `runners` and `hubTokens()` follow the new name, under the same id. Throws when the host is running, or
   * names no runner `from` or one already named `to`.
   */
  renameRunner(from: string, to: string): void;
  /** Deletes the stopped host's store, `data/host.db` and its sidecars; the next boot builds it anew. Throws when the host is running. */
  removeStore(): void;
  /**
   * The headers a worker of lease `leaseId` calls the runner with: the lease token the runner exported to it, as the
   * mock harness recorded at spawn, as a bearer or, with `'lease-token'`, in `X-Blizzard-Lease-Token` as the worker's
   * exporter sends it. Throws when no spawned session recorded that lease.
   */
  workerHeaders(leaseId: string, form?: 'bearer' | 'lease-token'): Record<string, string>;
  /** The installed `blizzard` CLI, by absolute path: a worker's own `PATH` need not hold it, as under `npx playwright test`. */
  readonly cliBinary: string;
  /**
   * Merges `overlay` over the stopped host's file, checked as a profile is; the next boot reads it. Throws naming every
   * problem, writing nothing, when the overlay is unsound or the host is running.
   */
  reconfigure(overlay: TomlTable): void;
  /** Removes the transcripts root, as an operator's rotation does. */
  rotateTranscripts(): void;
  /**
   * Runs the real `blizzard-runner` on this host's directory, under the host's isolated environment. A non-zero exit is
   * the result, not an error.
   */
  cli(args: string[]): Promise<CliResult>;
  /** Change documented host files only while stopped; tokens must have been minted by enrollment, not this seam. */
  prepareStopped(layout: { hostToken?: string; runnerTokens?: Record<string, string>; runnerSubscriptions?: Record<string, string>; runnerIds?: Record<string, string> }): void;
  /** Whether the winter trap has been run since the host was built; `false` on a host built without `runnerWinterTrap`. */
  winterInvoked(): boolean;
  /** The commit a branch of the minted world's bare origin `repo` points at, or `undefined` when the branch is not there. */
  originHead(repo: string, branch: string): Promise<string | undefined>;
  /** The session records the mock harness kept in the minted workspace, each parsed, in file-name order. */
  mockSessions(): Promise<MockSessionRecord[]>;
  /** What a stub or a bundle hook appended to the log `name` under the host's `${logs}`, or `undefined` before it wrote any. */
  workerLog(name: string): string | undefined;
  /** The session documents `mock-opencode` exported under the host's own transcripts root, each parsed, in file-name order. */
  opencodeExports(): Promise<OpencodeExport[]>;
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

/** One session the mock harness recorded: its invocations in order. The mock owns the rest of the shape. */
export interface MockSessionRecord {
  invocations: { kind: string }[];
  [key: string]: unknown;
}

/** One `mock-opencode` session export: the session's info and its messages' parts. */
export interface OpencodeExport {
  info: { id: string; parentID?: string | null; [key: string]: unknown };
  messages: { info: { role: string; [key: string]: unknown }; parts: { type: string; tool?: string; cost?: number; tokens?: { input: number }; [key: string]: unknown }[] }[];
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

/** The tokens a host's `.env` holds: the host's own, and each runner's by runner name. */
export interface HostHubTokens {
  host: string | undefined;
  runners: Record<string, string | undefined>;
}

interface EnvVariable {
  name: string;
  name_rule: { prefix: string; replace_pattern: string; replacement: string; upper_case: boolean } | null;
}

/** The `env` section of `contracts/config/host.json`: the file and the variables enrollment writes there. */
function hostEnvContract(): { file: string; variables: EnvVariable[] } {
  const document = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'config', 'host.json'), 'utf8')) as {
    env: { file: string; variables: EnvVariable[] };
  };
  return document.env;
}

/** The `.env` assignments, as the runner reads them: comments skipped, one pair of quotes stripped, the last wins. */
export function parseDotenv(text: string): Record<string, string> {
  const assigned: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const stripped = line.trim();
    if (stripped === '' || stripped.startsWith('#') || stripped.startsWith(';') || !stripped.includes('=')) continue;
    const at = stripped.indexOf('=');
    let value = stripped.slice(at + 1).trim();
    if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value.at(-1) === value[0]) value = value.slice(1, -1);
    assigned[stripped.slice(0, at).trim()] = value;
  }
  return assigned;
}

/** The host's issued tokens, by the contract's variable names. */
export function readHubTokens(dir: string, runnerNames: string[]): HostHubTokens {
  const contract = hostEnvContract();
  const file = path.join(dir, contract.file);
  const assigned = fs.existsSync(file) ? parseDotenv(fs.readFileSync(file, 'utf8')) : {};
  const fixed = contract.variables.find((variable) => variable.name_rule === null);
  if (fixed === undefined) throw new Error(`contracts/config/host.json declares no host token variable: ${JSON.stringify(contract)}`);
  return { host: assigned[fixed.name], runners: Object.fromEntries(runnerNames.map((name) => [name, assigned[runnerTokenVariable(name)]])) };
}

/** The variable the host contract's name rule derives for a runner's hub token. */
function runnerTokenVariable(runnerName: string): string {
  const rule = hostEnvContract().variables.find((variable) => variable.name_rule !== null)?.name_rule;
  if (rule == null) throw new Error('contracts/config/host.json declares no per-runner token variable');
  return rule.prefix + (rule.upper_case ? runnerName.toUpperCase() : runnerName).replace(new RegExp(rule.replace_pattern, 'g'), rule.replacement);
}

/** The contract's path entries, for the file a handle edit touches. */
function hostPath(dir: string, contractPath: string): string {
  const document = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'config', 'host.json'), 'utf8')) as { paths: { path: string }[] };
  if (!document.paths.some((entry) => entry.path === contractPath)) throw new Error(`contracts/config/host.json declares no path ${contractPath}`);
  return path.join(dir, ...contractPath.split('/'));
}

/** The lease credential the mock harness recorded for the session spawned under `leaseId`, scanning `stateDir`. */
export function recordedLeaseToken(stateDir: string, leaseId: string): string {
  const files = fs.existsSync(stateDir) ? fs.readdirSync(stateDir).filter((name) => name.endsWith('.json')) : [];
  for (const name of files) {
    const state = JSON.parse(fs.readFileSync(path.join(stateDir, name), 'utf8')) as { spawn_credential?: { lease_id: string | null; lease_token: string | null } | null };
    const credential = state.spawn_credential;
    if (credential?.lease_id === leaseId && credential.lease_token != null) return credential.lease_token;
  }
  throw new Error(`no mock session under ${stateDir} recorded a spawn credential for lease ${leaseId} (${files.length} sessions read)`);
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
export function checkHostOptions(profile: TomlTable, runnerEnv: Record<string, string>, winterTrap = false): string[] {
  const problems = [...checkProfile(profile, { contract: 'host', placeholders: PLACEHOLDERS }), ...checkOwnedKeys(profile), ...checkRunnerEnv(runnerEnv)];
  if (winterTrap && 'PATH' in runnerEnv) problems.push('runnerEnv PATH: runnerWinterTrap puts the trap first on PATH, so a test cannot also set it');
  return problems;
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
    let options: RunnerHostOptions = { ...DEFAULT_RUNNER_OPTIONS, ...given };
    if (options.runnerTraceEndpoint !== undefined && options.runnerOtlp !== false) throw new Error('runnerTraceEndpoint and runnerOtlp cannot both be set');
    const profile = profileFor(testInfo.file, options.runnerConfig);
    const problems = checkHostOptions(profile, options.runnerEnv, options.runnerWinterTrap);
    if (problems.length > 0) throw new Error(`the runner host options are unsound:\n${problems.join('\n')}`);

    const adopted = options.runnerAdopted;
    if (adopted !== undefined && options.runnerStarted) throw new Error('runnerAdopted needs runnerStarted: false; the first boot is the adopted runner registering');
    const index = count++;
    // A hub holds one live host per name and a host is named for its first runner, so a test's later hosts each name theirs.
    const defaultRunner = adopted?.name ?? (index === 0 ? FIRST_RUNNER : `${FIRST_RUNNER}-${index}`);
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
    let otlp: OtlpSink | undefined;

    const dispose = async (failed: boolean): Promise<string[]> => {
      if (disposed) return [];
      disposed = true;
      if (daemon !== undefined) await stopDaemon(daemon);
      await otlp?.close();
      const refusals = scanLog(logPath).map((line) => line.raw);
      if (failed && fs.existsSync(logPath)) await testInfo.attach(`runner host ${index} log`, { body: logTail(logPath), contentType: 'text/plain' });
      await testInfo.attach(`runner host ${index} timings`, { body: JSON.stringify(timings), contentType: 'application/json' });
      fs.rmSync(root, { recursive: true, force: true });
      return refusals;
    };
    made.push({ dispose });

    try {
      fs.mkdirSync(home, { recursive: true });
      const bundle = path.join(root, 'bundle');
      const stubs = path.join(root, 'stubs');
      const logs = path.join(root, 'logs');
      fs.mkdirSync(logs, { recursive: true });
      const placed = (content: string): string => content.replaceAll('${logs}', logs);
      for (const [file, content] of Object.entries(options.runnerBundle)) {
        const target = path.join(bundle, file);
        if (!path.resolve(target).startsWith(`${bundle}${path.sep}`)) throw new Error(`runnerBundle ${file}: a bundle file lies under the bundle directory`);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, placed(content));
      }
      for (const [file, content] of Object.entries(options.runnerStubs)) {
        const target = path.join(stubs, file);
        if (!path.resolve(target).startsWith(`${stubs}${path.sep}`)) throw new Error(`runnerStubs ${file}: a stub lies under the stubs directory`);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, placed(content), { mode: 0o755 });
      }
      const transcripts = path.join(root, 'transcripts');
      if (options.runnerOtlp !== false) otlp = await startOtlpSink({ hang: options.runnerOtlp === 'hung' });
      const priced = Object.entries(options.runnerModelPrices);
      if (priced.length > 0) {
        const cache = path.join(root, 'priced-cache');
        fs.mkdirSync(path.join(cache, 'opencode'), { recursive: true });
        const models = Object.fromEntries(priced.map(([provider, rates]) => [provider, { models: Object.fromEntries(Object.entries(rates).map(([model, cost]) => [model, { cost }])) }]));
        fs.writeFileSync(path.join(cache, 'opencode', 'models.json'), JSON.stringify(models));
        options = { ...options, runnerEnv: { XDG_CACHE_HOME: cache, ...options.runnerEnv } };
      }
      const env = isolatedEnv(home, path.join(home, '.config'), options.runnerEnv);
      const trapDir = path.join(root, 'trap');
      const trapSentinel = path.join(trapDir, 'winter-invoked');
      if (options.runnerWinterTrap) {
        fs.mkdirSync(trapDir, { recursive: true });
        fs.writeFileSync(path.join(trapDir, 'winter'), `#!/bin/sh\ntouch '${trapSentinel}'\nexit 99\n`, { mode: 0o755 });
        env.PATH = [trapDir, env.PATH].filter((entry) => entry !== undefined && entry !== '').join(path.delimiter);
      }
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
      writeOpencodeCredential(home);

      const runner = requireBinary(binDir(), RUNNER_BINARY);
      const operatorHome = ctx.hub.operatorHome === undefined ? path.join(home, '.config') : await ctx.hub.operatorHome();
      started = Date.now();
      if (index > 0 || adopted !== undefined) {
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
      // An adopted runner's host is never registered: the scaffold above is the whole of `init`.
      if (adopted === undefined) {
        const init = await run(runner, ['init', dir, '--hub', ctx.hub.url, ...(ctx.hub.tenantId === undefined ? [] : ['--tenant', ctx.hub.tenantId])], operatorHome);
        const hostId = /\((hs_[0-9A-Z]+)\)/.exec(init.stdout + init.stderr)?.[1];
        if (hostId !== undefined) ctx.ledger.add('host', hostId);
      }
      timings.initMs = Date.now() - started;

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
      // `init` scaffolds the `anthropic` subscription and has each runner name it; a host declaring none starts without both.
      if (!options.runnerSubscribed) {
        delete written.subscription;
        for (const entry of (written.runner ?? []) as TomlTable[]) delete entry.subscription;
      }
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
        subscription: options.runnerSubscribed
          ? [
              options.runnerSampling
                ? { slug: 'anthropic', usage_api_base: ctx.vendor.url, credentials_path: ctx.vendor.mintAnthropic(path.join(root, 'vendor')).path, sample_interval_seconds: 0 }
                : { slug: 'anthropic', usage_api_base: ctx.vendor.url, credentials_path: path.join(root, 'vendor', 'absent.json') },
            ]
          : [],
        runner: runnerEntries(written).map((entry) => ({
          name: entry.name,
          transcripts_root: transcripts,
          claude_code: { binary: path.join(mockBinDir(), 'mock-claude-code') },
          opencode: { enabled: false },
          worker: { env_passthrough: [FENCE_VAR, 'BZ_TRANSCRIPTS_ROOT'] },
        })),
      };
      const expanded = expandProfile(profile, { workspace, origins, mockBin: mockBinDir(), usageBase: ctx.vendor.url, bundle, stubs });
      const merged = mergeHostProfile(mergeHostProfile(written, baseline), expanded);
      fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(merged));

      const entries = runnerEntries(readHostFile(dir));
      // An adopted runner is the test's own minting, already in its ledger.
      if (adopted === undefined) for (const entry of entries) ctx.ledger.add('runner', entry.id);

      // The default runner's root, which the mock worker reads as `BZ_TRANSCRIPTS_ROOT`; a `runnerEnv` naming it wins.
      const transcriptsRoot = (): string => {
        const file = readHostFile(dir);
        const entry = ((file.runner ?? []) as TomlTable[]).find((candidate) => candidate.name === defaultRunner);
        return options.runnerEnv.BZ_TRANSCRIPTS_ROOT ?? (entry?.transcripts_root as string | undefined) ?? path.join(root, 'transcripts');
      };
      // The host and the CLI that reaches it export to the sink; the commands that build the host do not.
      const hostEnv = (): NodeJS.ProcessEnv => ({
        BZ_TRANSCRIPTS_ROOT: transcriptsRoot(),
        ...env,
        ...(otlp === undefined && options.runnerTraceEndpoint === undefined ? {} : { OTEL_EXPORTER_OTLP_ENDPOINT: otlp?.endpoint ?? options.runnerTraceEndpoint }),
      });
      const sessionsDir = options.runnerEnv.BLIZZARD_MOCK_HARNESS_STATE_DIR ?? path.join(workspace, '.blizzard-mock-harness', 'sessions');

      const boot = async (): Promise<void> => {
        const begun = Date.now();
        daemon = await startRunnerHost({ binary: runner, dir, port, loop: options.runnerLoop, logPath, env: hostEnv() });
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

      const reads = originReads(origins, env, root);
      const host: RunnerHost = {
        ...reads,
        dir,
        workspace,
        url,
        socket,
        home,
        runners,
        api,
        socketApi: sock,
        get pid() {
          return daemon?.pid;
        },
        logPath,
        otlp,
        get transcriptsRoot() {
          return transcriptsRoot();
        },
        logTail: () => logTail(logPath),
        log: () => (fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8') : ''),
        hubTokens: () => readHubTokens(dir, entries.map((entry) => entry.name)),
        clearHubTokens() {
          if (daemon !== undefined && isAlive(daemon.pid)) throw new Error('the host is running; stop() it before clearHubTokens()');
          fs.writeFileSync(path.join(dir, hostEnvContract().file), '');
        },
        renameRunner(from, to) {
          if (daemon !== undefined && isAlive(daemon.pid)) throw new Error('the host is running; stop() it before renameRunner()');
          const file = readHostFile(dir);
          const renamed = ((file.runner ?? []) as TomlTable[]).find((entry) => entry.name === from);
          if (renamed === undefined) throw new Error(`the host file names no runner ${from}`);
          if (entries.some((entry) => entry.name === to)) throw new Error(`the host already has a runner named ${to}`);
          renamed.name = to;
          fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(file));
          const envFile = hostPath(dir, hostEnvContract().file);
          if (fs.existsSync(envFile)) {
            const moved = fs
              .readFileSync(envFile, 'utf8')
              .split('\n')
              .map((line) => (line.trim().startsWith(`${runnerTokenVariable(from)}=`) ? line.replace(runnerTokenVariable(from), runnerTokenVariable(to)) : line));
            fs.writeFileSync(envFile, moved.join('\n'));
          }
          const own = hostPath(dir, 'runners');
          if (fs.existsSync(path.join(own, from))) fs.renameSync(path.join(own, from), path.join(own, to));
          const entry = entries.find((candidate) => candidate.name === from);
          if (entry !== undefined) entry.name = to;
          const known = runners[from];
          if (known !== undefined) {
            delete runners[from];
            runners[to] = { ...known, name: to };
          }
        },
        removeStore() {
          if (daemon !== undefined && isAlive(daemon.pid)) throw new Error('the host is running; stop() it before removeStore()');
          const store = hostPath(dir, 'data/host.db');
          for (const suffix of ['', '-wal', '-shm', '-journal']) fs.rmSync(`${store}${suffix}`, { force: true });
        },
        cliBinary: requireBinary(binDir(), 'blizzard'),
        workerHeaders(leaseId, form = 'bearer'): Record<string, string> {
          const token = recordedLeaseToken(sessionsDir, leaseId);
          return form === 'bearer' ? { Authorization: `Bearer ${token}` } : { 'X-Blizzard-Lease-Token': token };
        },
        reconfigure(overlay) {
          const problems = [...checkProfile(overlay, { contract: 'host', placeholders: PLACEHOLDERS }), ...checkOwnedKeys(overlay)];
          if (daemon !== undefined && isAlive(daemon.pid)) problems.push('the host is running; stop() it before reconfigure()');
          if (problems.length > 0) throw new Error(`the host overlay is unsound:\n${problems.join('\n')}`);
          const placed = expandProfile(overlay, { workspace, origins, mockBin: mockBinDir(), usageBase: ctx.vendor.url });
          fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(mergeHostProfile(readHostFile(dir), placed)));
        },
        rotateTranscripts: () => fs.rmSync(transcriptsRoot(), { recursive: true, force: true }),
        cli: (args) => runCommand(runner, args, { env: { ...hostEnv(), BZ_RUNNER_DIR: dir }, cwd: root }),
        prepareStopped(layout) {
          if (daemon !== undefined && isAlive(daemon.pid)) throw new Error('stop the host before changing its documented files');
          const file = readHostFile(dir);
          const configured = (file.runner ?? []) as TomlTable[];
          const names = new Set(configured.map((entry) => entry.name));
          for (const name of [
            ...Object.keys(layout.runnerSubscriptions ?? {}),
            ...Object.keys(layout.runnerIds ?? {}),
            ...Object.keys(layout.runnerTokens ?? {}),
          ]) {
            if (!names.has(name)) throw new Error(`the host has no runner ${name}`);
          }
          const tokens: Record<string, string> = {};
          if (layout.hostToken !== undefined) tokens.BZ_HOST_TOKEN = layout.hostToken;
          for (const [name, token] of Object.entries(layout.runnerTokens ?? {})) {
            tokens[`BZ_HUB_TOKEN_${name.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`] = token;
          }
          for (const [key, token] of Object.entries(tokens)) {
            if (/\r|\n/.test(token)) throw new Error(`token for ${key} contains a newline`);
          }
          for (const [name, subscription] of Object.entries(layout.runnerSubscriptions ?? {})) {
            configured.find((item) => item.name === name)!.subscription = subscription;
          }
          for (const [name, id] of Object.entries(layout.runnerIds ?? {})) {
            configured.find((item) => item.name === name)!.id = id;
          }
          let envText: string | undefined;
          if (Object.keys(tokens).length > 0) {
            const envFile = path.join(dir, '.env');
            envText = fs.existsSync(envFile) ? fs.readFileSync(envFile, 'utf8') : '';
            for (const [key, token] of Object.entries(tokens)) {
              envText = envText.split('\n').filter((line) => !line.startsWith(`${key}=`)).join('\n').trimEnd() + `\n${key}=${token}\n`;
            }
          }
          if (layout.runnerSubscriptions !== undefined || layout.runnerIds !== undefined) fs.writeFileSync(path.join(dir, 'blizzard-host.toml'), stringify(file));
          if (envText !== undefined) fs.writeFileSync(path.join(dir, '.env'), envText, { mode: 0o600 });
          for (const [name, id] of Object.entries(layout.runnerIds ?? {})) {
            runners[name] = { ...api.runner(id), id, name };
          }
        },
        winterInvoked: () => fs.existsSync(trapSentinel),
        async originHead(repo, branch) {
          const result = await runCommand('git', ['--git-dir', path.join(origins, `${repo}.git`), 'rev-parse', '--verify', '--quiet', `refs/heads/${branch}`], { env, cwd: root });
          return result.code === 0 ? result.stdout.trim() : undefined;
        },
        workerLog: (name) => (fs.existsSync(path.join(logs, name)) ? fs.readFileSync(path.join(logs, name), 'utf8') : undefined),
        mockSessions: () => readJsonDocuments(path.join(workspace, '.blizzard-mock-harness', 'sessions')),
        opencodeExports: () => readJsonDocuments(path.join(transcripts, 'mock-opencode')),
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
      if (adopted !== undefined) host.prepareStopped({ runnerIds: { [adopted.name]: adopted.runnerId }, runnerTokens: { [adopted.name]: adopted.token } });
      return host;
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
    // The mock's own knobs never reach the host; only the fence set below does.
    if (name.startsWith('BLIZZARD_MOCK_')) delete env[name];
  }
  Object.assign(env, runnerEnv);
  return { ...env, [FENCE_VAR]: '1' };
}

/** The Claude credential the harness-health probe reads at the private home's default path: synthetic, and far from expiry. */
function writeClaudeCredential(home: string): void {
  const file = path.join(home, '.claude', '.credentials.json');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ claudeAiOauth: { accessToken: 'synthetic-claude-login', refreshToken: 'synthetic', expiresAt: Date.UTC(2099, 0, 1) } }), { mode: 0o600 });
}

/** The OpenCode credential the harness-health probe reads at the private data home's default path: synthetic, and only non-empty. */
function writeOpencodeCredential(home: string): void {
  const file = path.join(home, '.local', 'share', 'opencode', 'auth.json');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ synthetic: { type: 'api', key: 'synthetic-opencode-login' } }), { mode: 0o600 });
}

/** Every `*.json` file of `dir`, parsed, in file-name order; none when the directory is not there. */
async function readJsonDocuments<T>(dir: string): Promise<T[]> {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.json'))
    .sort()
    .map((name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')) as T);
}
