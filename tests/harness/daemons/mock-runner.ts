import * as path from 'node:path';
import { daemonEnv, mockBinDir, requireBinary } from '../env';
import { type DaemonRecord, startDaemon } from './process';

/**
 * A mock runner against `hubUrl`, under a token minted in the test's tenant. It is never started without `--token`:
 * a mock runner left to add itself would name no tenant and be refused.
 */
export function startMockRunner(dir: string, options: { hubUrl: string; name: string; token: string }): Promise<DaemonRecord> {
  const binary = requireBinary(mockBinDir(), 'blizzard-mock-runner');
  return startDaemon({
    name: `mock-runner-${options.name}`,
    cwd: dir,
    logPath: path.join(dir, `mock-runner-${options.name}.log`),
    healthPath: '/api/health',
    launch: (port) => ({
      command: binary,
      args: [
        '--host', '127.0.0.1', '--port', String(port),
        '--hub-url', options.hubUrl, '--name', options.name, '--token', options.token,
      ],
      env: daemonEnv({ BZ_HUB_URL: options.hubUrl }),
    }),
  });
}
