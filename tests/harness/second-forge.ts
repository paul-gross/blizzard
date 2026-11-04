import { startForge } from './daemons/forge';
import { type DaemonRecord, stopDaemon } from './daemons/process';

/** Own the forge from the moment it starts, including failures while constructing its client. */
export async function withSecondForge<T>(dir: string, use: (daemon: DaemonRecord) => Promise<T>, env: Record<string, string> = {}): Promise<T> {
  const daemon = await startForge(dir, env);
  try {
    return await use(daemon);
  } finally {
    await stopDaemon(daemon);
  }
}
