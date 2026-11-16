import * as fs from 'node:fs';
import * as path from 'node:path';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { parse, stringify, type TomlTable } from 'smol-toml';

const root = path.resolve(import.meta.dirname, '../../..');
const scratch = process.env.BLIZZARD_TMPDIR;
const runtime = process.env.BZ_RUNNER_RUNTIME;
const port = process.env.BZ_RUNNER_PORT;
const winterEnv = process.env.WINTER_ENV;
if (!scratch || !path.isAbsolute(scratch) || !runtime || !port || !winterEnv) {
  throw new Error('source `winter env <env> --resolve` and set BLIZZARD_TMPDIR to an absolute scratch directory');
}
if (!process.argv[2]) throw new Error('supply a command to run while the candidate runner is serving');
const scenario = process.env.OPENCODE2_HEALTH_SCENARIO ?? 'NEVER_REGISTERED';
if (scenario !== 'available' && scenario !== 'NEVER_REGISTERED') throw new Error('OPENCODE2_HEALTH_SCENARIO must be available or NEVER_REGISTERED');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'opencode2-health-'));
fs.chmodSync(dir, 0o700);
const home = path.join(dir, 'home');
const host = path.join(dir, 'host');
const binary = path.join(dir, 'opencode');
fs.mkdirSync(home);
const env: NodeJS.ProcessEnv = { ...process.env, HOME: home, XDG_CONFIG_HOME: path.join(home, '.config'), XDG_DATA_HOME: path.join(home, '.local/share'), XDG_CACHE_HOME: path.join(home, '.cache'), TMPDIR: dir, PATH: `/usr/bin:/bin:${process.env.PATH ?? ''}`, BLIZZARD_MOCK_HARNESS_FENCE: '1' };
for (const key of Object.keys(env)) {
  if (key.startsWith('BZ_HUB_TOKEN') || key.startsWith('OTEL_') || key.startsWith('BLIZZARD_MOCK_') && key !== 'BLIZZARD_MOCK_HARNESS_FENCE') delete env[key];
}
const auth = path.join(home, '.local/share/opencode/auth.json');
fs.mkdirSync(path.dirname(auth), { recursive: true });
fs.writeFileSync(auth, JSON.stringify({ synthetic: { type: 'api', key: 'synthetic' } }), { mode: 0o600 });
function run(command: string, args: string[]): void {
  const result = spawnSync(command, args, { cwd: root, env, stdio: 'inherit', timeout: 120_000 });
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed (${result.status ?? result.error})`);
}
function wait(child: ChildProcess): Promise<number> {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve(child.exitCode ?? 128);
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve(code ?? (signal ? 128 : 1)));
  });
}
let candidate: ChildProcess | undefined;
let command: ChildProcess | undefined;
let restore = false;
let interrupted = false;
let interrupt!: () => void;
const interruption = new Promise<void>((resolve) => { interrupt = resolve; });
function signalGroup(child: ChildProcess | undefined, signal: NodeJS.Signals): void {
  if (!child?.pid) return;
  try { process.kill(-child.pid, signal); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
  }
}
async function stopGroups(children: (ChildProcess | undefined)[]): Promise<void> {
  for (const child of children) signalGroup(child, 'SIGTERM');
  // Signal the group again even if its leader exited before its descendants.
  await new Promise((resolve) => setTimeout(resolve, 2_000));
  for (const child of children) signalGroup(child, 'SIGKILL');
}
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => {
  interrupted = true;
  interrupt();
});
try {
  run(path.join(root, '../blizzard-mock/.venv/bin/mock-opencode-2'), ['emit-engine', '--out', binary, ...(scenario === 'NEVER_REGISTERED' ? ['--lever', scenario] : [])]);
  restore = true;
  run('winter', ['service', 'down', `${winterEnv}/runner`]);
  if (interrupted) throw new Error('interrupted during setup');
  fs.cpSync(runtime, host, { recursive: true, filter: (source) => {
    let stat: fs.Stats;
    try { stat = fs.lstatSync(source); } catch (error) {
      // Sockets and pid files can vanish while the stopped runner finishes exiting.
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
      throw error;
    }
    if (stat.isSymbolicLink()) throw new Error(`runtime contains a symlink: ${source}`);
    if (stat.isSocket()) return false;
    if (!stat.isDirectory() && !stat.isFile()) throw new Error(`runtime contains a special file: ${source}`);
    return true;
  } });
  const file = path.join(host, 'blizzard-host.toml');
  const config = parse(fs.readFileSync(file, 'utf8'));
  // The copied store may contain active synthetic leases. Rebind only the copy,
  // so its startup reconciliation can never sweep the original scratch root.
  const workerScratch = path.join(dir, 'worker-scratch');
  config.worker_scratch = { root: workerScratch };
  fs.writeFileSync(path.join(host, 'data', 'worker-scratch-root'), workerScratch);
  for (const runner of config.runner as TomlTable[]) {
    runner.claude_code = { enabled: false };
    runner.opencode = { ...(runner.opencode as TomlTable), enabled: true, binary, worker_config_path: path.join(dir, 'worker-config.json') };
    runner.transcripts_root = path.join(dir, 'transcripts');
    runner.worker_settings_path = path.join(dir, 'worker-settings.json');
  }
  fs.writeFileSync(file, stringify(config));
  const log = fs.openSync(path.join(dir, 'runner.log'), 'w', 0o600);
  candidate = spawn(path.join(root, '.venv/bin/blizzard'), ['runner', 'host', '--dir', host, '--host', '127.0.0.1', '--port', port], { cwd: root, env, detached: true, stdio: ['ignore', log, log] });
  fs.closeSync(log);
  const exited = wait(candidate);
  let ready = false;
  for (let i = 0; i < 120 && !interrupted && candidate.exitCode === null; i++) {
    try { ready = (await fetch(`http://127.0.0.1:${port}/api/health`, { signal: AbortSignal.timeout(1_000) })).ok; } catch { /* still starting */ }
    if (ready) break;
    await Promise.race([new Promise((resolve) => setTimeout(resolve, 500)), interruption]);
  }
  if (!ready) throw new Error(`candidate did not become ready; see ${dir}/runner.log`);
  console.log(`Candidate ready; evidence: ${dir}`);
  command = spawn(process.argv[2], process.argv.slice(3), { cwd: root, env: { ...env, BZ_RUNNER_RUNTIME: host, OPENCODE2_HEALTH_DIR: dir }, detached: true, stdio: 'inherit' });
  const result = await Promise.race([wait(command), exited.then(() => { throw new Error('candidate runner exited during command'); }), interruption.then(() => 1)]);
  if (result !== 0 || interrupted) process.exitCode = result || 1;
} finally {
  try {
    await stopGroups([command, candidate]);
  } finally {
    if (restore) run('winter', ['service', 'up', `${winterEnv}/runner`, '--wait']);
    console.log(`Evidence retained at ${dir}`);
  }
}
