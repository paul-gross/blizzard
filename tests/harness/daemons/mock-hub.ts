import * as path from 'node:path';
import { daemonEnv, mockBinDir, requireBinary } from '../env';
import { type LiveDaemon, startDaemon } from './process';

/**
 * `blizzard-mock-hub` in `dir`, its log appended to `<dir>/mock-hub.log`. A `port` pins it to a URL it held before, so a
 * restarted mock hub answers where a runner already points.
 */
export function startMockHub(dir: string, options: { port?: number } = {}): Promise<LiveDaemon> {
  const binary = requireBinary(mockBinDir(), 'blizzard-mock-hub');
  return startDaemon(
    {
      name: 'mock-hub',
      cwd: dir,
      logPath: path.join(dir, 'mock-hub.log'),
      healthPath: '/api/health',
      launch: (port) => ({
        command: binary,
        args: ['--host', '127.0.0.1', '--port', String(port)],
        env: daemonEnv(),
      }),
    },
    options.port,
  );
}
