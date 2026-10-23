import * as path from 'node:path';
import { daemonEnv, mockBinDir, requireBinary } from '../env';
import { type DaemonRecord, startDaemon } from './process';

/** The stub identity provider. */
export function startIdp(dir: string): Promise<DaemonRecord> {
  const binary = requireBinary(mockBinDir(), 'blizzard-mock-idp');
  return startDaemon({
    name: 'idp',
    cwd: dir,
    logPath: path.join(dir, 'idp.log'),
    healthPath: '/healthz',
    launch: (port) => ({
      command: binary,
      args: ['--host', '127.0.0.1', '--port', String(port)],
      env: daemonEnv(),
    }),
  });
}
