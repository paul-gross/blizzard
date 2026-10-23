import * as path from 'node:path';
import { daemonEnv, mockBinDir, requireBinary } from '../env';
import { type DaemonRecord, startDaemon } from './process';

/** The mock forge, its repositories directory starting empty. */
export function startForge(dir: string): Promise<DaemonRecord> {
  const binary = requireBinary(mockBinDir(), 'blizzard-mock-forge');
  return startDaemon({
    name: 'forge',
    cwd: dir,
    logPath: path.join(dir, 'forge.log'),
    healthPath: '/healthz',
    launch: (port) => ({
      command: binary,
      args: ['--repos-dir', path.join(dir, 'origins'), '--host', '127.0.0.1', '--port', String(port)],
      env: daemonEnv(),
    }),
  });
}
