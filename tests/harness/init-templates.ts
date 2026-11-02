import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { initHub } from './daemons/hub';
import { binDir, daemonEnv, readContract, requireBinary, runDir } from './env';
import { runOnce } from './run-once';

const run = promisify(execFile);

/** The store file a runner host keeps its state in, relative to the host directory. */
export const RUNNER_STORE = path.join('data', 'host.db');

function templatesDir(): string {
  return path.join(runDir(), 'templates');
}

/**
 * A hub directory `blizzard-hub init` wrote, built once per run with an empty profile and returned as the template
 * directory (the one holding `hub/`). Every stack restores it instead of running `init` again.
 */
export async function hubTemplate(): Promise<string> {
  const dir = path.join(templatesDir(), 'hub');
  await runOnce('hub-template', () => initHub(dir, {}).then(() => undefined), { dir: templatesDir() });
  return dir;
}

/**
 * The runner store `blizzard-runner init` migrates before it reaches the hub, built once per run against an address
 * nothing answers on (so `init` exits non-zero by design, after the migration), and returned as the path of its
 * `data/host.db`. It holds no identity: the file is the schema alone.
 */
export async function runnerStoreTemplate(): Promise<string> {
  const root = path.join(templatesDir(), 'runner');
  const store = path.join(root, 'h', RUNNER_STORE);
  await runOnce('runner-store-template', () => buildRunnerStore(root), { dir: templatesDir() });
  return store;
}

async function buildRunnerStore(root: string): Promise<void> {
  const binary = requireBinary(binDir(), 'blizzard-runner');
  const home = path.join(root, 'home');
  fs.mkdirSync(home, { recursive: true });
  const env = daemonEnv({ HOME: home, XDG_DATA_HOME: path.join(home, '.local', 'share'), XDG_CACHE_HOME: path.join(home, '.cache') }, path.join(home, '.config'));
  const pinned = Object.keys(readContract().set);
  for (const name of Object.keys(env)) {
    // The same scrub the host builder's CLIs get: every `BZ_*` is a seed or a credential, the pinned ones aside.
    if (name.startsWith('BZ_') && !pinned.includes(name)) delete env[name];
  }
  const dir = path.join(root, 'h');
  try {
    await run(binary, ['init', dir, '--hub', 'http://127.0.0.1:9'], { env, cwd: root });
  } catch {
    // Expected: the hub is unreachable, and `init` exits non-zero after it migrated the store.
  }
  const store = path.join(dir, RUNNER_STORE);
  if (!fs.existsSync(store)) throw new Error(`blizzard-runner init left no ${store}; the runner store template cannot be built`);
  for (const sidecar of ['-wal', '-shm']) {
    if (fs.existsSync(`${store}${sidecar}`)) throw new Error(`blizzard-runner init left ${store}${sidecar} beside the store; the template would not be self-contained`);
  }
}
