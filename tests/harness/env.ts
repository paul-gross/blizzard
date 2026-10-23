import * as fs from 'node:fs';
import * as path from 'node:path';

/** The `tests/` directory, the package root. */
export const TESTS_ROOT = path.resolve(__dirname, '..');
/** The `blizzard` checkout the package lives in. */
export const BLIZZARD_ROOT = path.resolve(TESTS_ROOT, '..');

/** `tests/daemon-env.json`: the environment contract both suites read. */
export interface DaemonEnvContract {
  set: Record<string, string>;
  unset: string[];
  unsetPrefixes: string[];
  isolatedConfigHome: boolean;
  refusals: string[];
}

export function readContract(): DaemonEnvContract {
  return JSON.parse(fs.readFileSync(path.join(TESTS_ROOT, 'daemon-env.json'), 'utf8')) as DaemonEnvContract;
}

/** The directory holding the installed `blizzard-hub`. */
export function binDir(): string {
  return process.env.BLIZZARD_BIN_DIR ?? path.join(BLIZZARD_ROOT, '.venv', 'bin');
}

/** The directory holding the installed mock fleet's binaries, the sibling `blizzard-mock` checkout's by default. */
export function mockBinDir(): string {
  return process.env.BLIZZARD_MOCK_BIN_DIR ?? path.join(BLIZZARD_ROOT, '..', 'blizzard-mock', '.venv', 'bin');
}

/** A daemon's binary, or a failure naming how to get one: the tier never skips, since a skipped tier reads as green. */
export function requireBinary(dir: string, name: string): string {
  const binary = path.join(dir, name);
  try {
    fs.accessSync(binary, fs.constants.X_OK);
  } catch {
    throw new Error(
      `${name} is not installed at ${binary}. Provision the environment with \`winter provision <env>\` ` +
        `(or \`uv sync\` in the checkout that owns it); BLIZZARD_BIN_DIR and BLIZZARD_MOCK_BIN_DIR name another directory.`,
    );
  }
  return binary;
}

/** This run's id, minted once in `playwright.config.ts` and shared with every worker through the environment. */
export function runId(): string {
  const id = process.env.BLIZZARD_RUN_ID;
  if (id === undefined || id === '') {
    throw new Error('BLIZZARD_RUN_ID is unset; playwright.config.ts mints it when the config loads.');
  }
  return id;
}

/** `.run/<run-id>/`, where this run's daemons, logs, and hub coordinates live. */
export function runDir(): string {
  return path.join(TESTS_ROOT, '.run', runId());
}

/**
 * A daemon's environment: the runner's own, less every `unset` name and every variable under an `unsetPrefixes`
 * prefix, plus `set`, plus `XDG_CONFIG_HOME` at the run's empty config home, plus what the daemon's start needs of its
 * own. Nothing `extra` carries can remove a `set` variable.
 */
export function daemonEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const contract = readContract();
  const env: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (contract.unset.includes(name)) continue;
    if (contract.unsetPrefixes.some((prefix) => name.startsWith(prefix))) continue;
    env[name] = value;
  }
  Object.assign(env, extra);
  if (contract.isolatedConfigHome) {
    const home = path.join(runDir(), 'config-home');
    fs.mkdirSync(home, { recursive: true });
    env.XDG_CONFIG_HOME = home;
  }
  return { ...env, ...contract.set };
}
