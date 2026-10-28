import * as path from 'node:path';
import type { Launch } from './process';
import { type LiveDaemon, startDaemon } from './process';

/**
 * `blizzard-runner host --dir <dir> --loop <loop>` on the port the host file pins, its log appended to `logPath`. The
 * port is the host file's own, so a restart binds the address the first start did.
 */
export function startRunnerHost(options: {
  binary: string;
  dir: string;
  port: number;
  loop: 'stepped' | 'timed';
  logPath: string;
  env: NodeJS.ProcessEnv;
}): Promise<LiveDaemon> {
  const launch = (): Launch => ({
    command: options.binary,
    args: ['host', '--dir', options.dir, '--loop', options.loop],
    env: options.env,
  });
  return startDaemon(
    {
      name: `runner-host-${path.basename(options.dir)}`,
      cwd: options.dir,
      logPath: options.logPath,
      healthPath: '/api/health',
      group: true,
      launch,
    },
    options.port,
  );
}
