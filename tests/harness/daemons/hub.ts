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
  mergeHubProfile(hubDir, profile);
  return hubDir;
}

/** What `docs/backup.md` names as the hub-data volume's durable content, relative to the hub directory. */
const DURABLE = [['blizzard-hub.toml'], ['data', 'hub.db'], ['data', 'auth', 'secret-keys']];

/**
 * `initHub`'s result without running `init`: copies the template's durable hub files (the ones `docs/backup.md` names,
 * never the log, the origins or the egress directory) into `<dir>/hub`, then merges the profile as `initHub` does.
 */
export function restoreHub(dir: string, template: string, profile: TomlTable): string {
  const hubDir = path.join(dir, 'hub');
  fs.mkdirSync(dir, { recursive: true });
  for (const parts of DURABLE) {
    const target = path.join(hubDir, ...parts);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.cpSync(path.join(template, 'hub', ...parts), target, { recursive: true });
  }
  mergeHubProfile(hubDir, profile);
  return hubDir;
}

function mergeHubProfile(hubDir: string, profile: TomlTable): void {
  if (Object.keys(profile).length === 0) return;
  const configPath = path.join(hubDir, 'blizzard-hub.toml');
  const written = parse(fs.readFileSync(configPath, 'utf8')) as TomlTable;
  fs.writeFileSync(configPath, stringify(mergeProfile(written, profile)));
}

/** The value of `BLIZZARD_TEST_IDP_SECRET`, which an `auth` profile's provider names; the stub IdP ignores it, the hub refuses to boot without it. */
export const IDP_SECRET = 'blizzard-tests-idp-secret';

/**
 * `host` against a hub directory `init` already wrote, its log appended to `<dir>/hub.log`. `env` reaches this process
 * only, after the contract's unset filter, and every hub carries `BLIZZARD_TEST_IDP_SECRET` beside it, a restarted one
 * included; a `port` pins the hub to a URL it held before.
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
        env: daemonEnv({ BLIZZARD_TEST_IDP_SECRET: IDP_SECRET, ...options.env }),
      }),
    },
    options.port,
  );
}

/**
 * A hub: the `template` directory restored, or `init` run when there is none, the profile merged over what it wrote,
 * then `host`.
 */
export async function startHub(dir: string, profile: TomlTable, env: Record<string, string> = {}, template?: string): Promise<LiveDaemon> {
  if (template === undefined) await initHub(dir, profile);
  else restoreHub(dir, template, profile);
  return hostHub(dir, { env });
}
