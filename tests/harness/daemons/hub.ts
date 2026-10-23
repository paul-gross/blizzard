import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { promisify } from 'node:util';
import { parse, stringify } from 'smol-toml';
import { binDir, daemonEnv, requireBinary } from '../env';
import { mergeProfile, type TomlTable } from '../profiles';
import { type DaemonRecord, startDaemon } from './process';

const run = promisify(execFile);

/**
 * A hub: `blizzard-hub init <dir>`, the profile merged over the TOML it produced, then `host`. `init` owns everything the
 * profile does not name, the store's location and the secret key among them.
 */
export async function startHub(dir: string, profile: TomlTable): Promise<DaemonRecord> {
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
  return startDaemon({
    name: 'hub',
    cwd: dir,
    logPath: path.join(dir, 'hub.log'),
    healthPath: '/api/health',
    launch: (port) => ({
      command: binary,
      args: ['host', '--dir', hubDir, '--host', '127.0.0.1', '--port', String(port)],
      env: daemonEnv(),
    }),
  });
}
