import * as path from 'node:path';
import { type LiveDaemon, startDaemon } from './process';

/**
 * `blizzard-runner host --dir <dir> --loop <loop>`, its log appended to `logPath`. Without a `port` it binds the one the
 * host file names, `0` on a first boot; a restart pins the `port` the first boot published, so its URL holds.
 */
export function startRunnerHost(options: {
  binary: string;
  dir: string;
  port?: number;
  loop: 'stepped' | 'timed';
  logPath: string;
  env: NodeJS.ProcessEnv;
}): Promise<LiveDaemon> {
  return startDaemon(
    {
      name: `runner-host-${path.basename(options.dir)}`,
      cwd: options.dir,
      logPath: options.logPath,
      group: true,
      launch: (port, listeningFile) => ({
        command: options.binary,
        args: ['host', '--dir', options.dir, '--loop', options.loop, '--port', String(port), '--listening-file', listeningFile],
        env: options.env,
      }),
    },
    options.port,
  );
}
