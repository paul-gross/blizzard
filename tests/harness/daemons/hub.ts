import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { promisify } from 'node:util';
import { parse, stringify } from 'smol-toml';
import { binDir, daemonEnv, requireBinary } from '../env';
import { mergeProfile, type TomlTable } from '../profiles';
import { type LiveDaemon, startDaemon } from './process';

const run = promisify(execFile);

/** `blizzard-hub init <dir>/hub` and the profile merged over the TOML it produced; returns the hub's directory. */
export async function initHub(dir: string, profile: TomlTable): Promise<string> {
  const binary = requireBinary(binDir(), 'blizzard-hub');
  const hubDir = path.join(dir, 'hub');
  fs.mkdirSync(dir, { recursive: true });
  try {
    await run(binary, ['init', hubDir], { env: daemonEnv(), cwd: dir });
  } catch (error) {
    const { stdout, stderr } = error as { stdout?: string; stderr?: string };
    throw new Error(`blizzard-hub init failed:\n${stdout ?? ''}${stderr ?? ''}`, { cause: error });
  }
  if (Object.keys(profile).length > 0) {
    const configPath = path.join(hubDir, 'blizzard-hub.toml');
    const written = parse(fs.readFileSync(configPath, 'utf8')) as TomlTable;
    fs.writeFileSync(configPath, stringify(mergeProfile(written, profile)));
  }
  return hubDir;
}

/**
 * `host` against a hub directory `init` already wrote, its log appended to `<dir>/hub.log`. `env` reaches this process
 * only, after the contract's unset filter; a `port` pins the hub to a URL it held before.
 */
export function hostHub(dir: string, options: { env?: Record<string, string>; port?: number } = {}): Promise<LiveDaemon> {
  const binary = requireBinary(binDir(), 'blizzard-hub');
  const hubDir = path.join(dir, 'hub');
  return startDaemon(
    {
      name: 'hub',
      cwd: dir,
      logPath: path.join(dir, 'hub.log'),
      healthPath: '/api/health',
      launch: (port) => ({
        command: binary,
        args: ['host', '--dir', hubDir, '--host', '127.0.0.1', '--port', String(port)],
        env: daemonEnv(options.env ?? {}),
      }),
    },
    options.port,
  );
}

/** A hub: `init`, the profile merged over what it wrote, then `host`. */
export async function startHub(dir: string, profile: TomlTable, env: Record<string, string> = {}): Promise<LiveDaemon> {
  await initHub(dir, profile);
  return hostHub(dir, { env });
}
