import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as path from 'node:path';

const START_TIMEOUT_MS = 40_000;
const STOP_GRACE_MS = 10_000;
const POLL_MS = 150;

/** What a daemon's start needs: the binary, its arguments for the port it was given, and its environment. */
export interface Launch {
  command: string;
  args: string[];
  env: NodeJS.ProcessEnv;
}

export interface DaemonSpec {
  name: string;
  /** Where the daemon runs, under the run's directory. */
  cwd: string;
  /** The one file its stdout and stderr are appended to. */
  logPath: string;
  /** The route answering `200` once the daemon is ready. */
  healthPath: string;
  launch: (port: number) => Launch;
}

/** A started daemon: what a test reaches it by, and what a later process needs to stop it. */
export interface DaemonRecord {
  name: string;
  pid: number;
  url: string;
  logPath: string;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A port taken by binding `0` on loopback, reading the port the system gave, and closing the socket. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as net.AddressInfo;
      server.close(() => resolve(port));
    });
  });
}

export function logTail(logPath: string, lines = 40): string {
  try {
    return fs.readFileSync(logPath, 'utf8').split('\n').slice(-lines).join('\n');
  } catch {
    return '(no log)';
  }
}

export function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function answers(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
    await response.arrayBuffer();
    return response.status === 200;
  } catch {
    return false;
  }
}

class AddressInUse extends Error {}

async function startOnce(spec: DaemonSpec): Promise<DaemonRecord> {
  const port = await freePort();
  const launch = spec.launch(port);
  fs.mkdirSync(spec.cwd, { recursive: true });
  fs.mkdirSync(path.dirname(spec.logPath), { recursive: true });
  const log = fs.openSync(spec.logPath, 'a');
  // Detached, so the daemon outlives the worker that started it; the teardown project stops it by pid.
  const child = spawn(launch.command, launch.args, {
    cwd: spec.cwd,
    env: launch.env,
    stdio: ['ignore', log, log],
    detached: true,
  });
  fs.closeSync(log);
  let exited: number | string | null = null;
  child.once('exit', (code, signal) => {
    exited = code ?? signal ?? 'unknown';
  });
  child.once('error', (error) => {
    exited = error.message;
  });
  child.unref();
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (exited !== null) {
      const tail = logTail(spec.logPath);
      const message = `${spec.name} exited early (${exited}):\n${tail}`;
      throw /address already in use|Errno 98/i.test(tail) ? new AddressInUse(message) : new Error(message);
    }
    if (await answers(`${url}${spec.healthPath}`)) {
      return { name: spec.name, pid: child.pid as number, url, logPath: spec.logPath };
    }
    await sleep(POLL_MS);
  }
  await stopDaemon({ pid: child.pid as number });
  throw new Error(`${spec.name} did not answer ${spec.healthPath} within ${START_TIMEOUT_MS / 1000}s:\n${logTail(spec.logPath)}`);
}

/** Starts a daemon and returns once it answers its health route. An address-in-use exit is retried once on a new port. */
export async function startDaemon(spec: DaemonSpec): Promise<DaemonRecord> {
  try {
    return await startOnce(spec);
  } catch (error) {
    if (!(error instanceof AddressInUse)) throw error;
    return startOnce(spec);
  }
}

/** `SIGTERM`, then `SIGKILL` if the daemon has not exited ten seconds later. */
export async function stopDaemon(daemon: { pid: number }): Promise<void> {
  if (!isAlive(daemon.pid)) return;
  process.kill(daemon.pid, 'SIGTERM');
  const deadline = Date.now() + STOP_GRACE_MS;
  while (Date.now() < deadline) {
    if (!isAlive(daemon.pid)) return;
    await sleep(POLL_MS);
  }
  try {
    process.kill(daemon.pid, 'SIGKILL');
  } catch {
    // It exited between the last check and the kill.
  }
}
