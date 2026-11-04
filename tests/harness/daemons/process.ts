import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

const START_TIMEOUT_MS = 40_000;
const STOP_GRACE_MS = 10_000;
const POLL_MS = 150;

/** What a daemon's start needs: the binary, its arguments, and its environment. */
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
  /** Its command for `--port <port>`, publishing its URL to `listeningFile` once it serves. */
  launch: (port: number, listeningFile: string) => Launch;
  /** Signal the daemon's whole process group, so whatever it spawned dies with it. */
  group?: boolean;
}

/** A started daemon: what a test reaches it by, and what a later process needs to stop it. */
export interface DaemonRecord {
  name: string;
  pid: number;
  url: string;
  logPath: string;
}

/** A daemon this process started: its record, and how it ended. `exited` never reaches a serialized record. */
export interface LiveDaemon extends DaemonRecord {
  /** Whether stopping it signals its process group. */
  group?: boolean;
  /** Resolves with the exit code, or the signal that ended it, once the process is gone. */
  exited: Promise<number | string>;
}

/** The serializable fields of a started daemon. */
export function recordOf(daemon: DaemonRecord): DaemonRecord {
  return { name: daemon.name, pid: daemon.pid, url: daemon.url, logPath: daemon.logPath };
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function logTail(logPath: string, lines = 40): string {
  try {
    return fs.readFileSync(logPath, 'utf8').split('\n').slice(-lines).join('\n');
  } catch {
    return '(no log)';
  }
}

/** Sends `signal` to the daemon, or to its whole process group when it was started with `group`. */
function signalDaemon(daemon: { pid: number; group?: boolean }, signal: NodeJS.Signals): void {
  process.kill(daemon.group === true ? -daemon.pid : daemon.pid, signal);
}

export function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** The URL a daemon published to `file`; `undefined` until it is there. The daemon renames it into place whole. */
function published(file: string): string | undefined {
  try {
    const url = fs.readFileSync(file, 'utf8').trim();
    return url === '' ? undefined : url;
  } catch {
    return undefined;
  }
}

/**
 * Spawns the daemon detached, its output appended to its log, and polls `ready` until it yields the daemon's URL. An
 * exit first fails the start with the log's tail; a timeout stops the daemon before failing.
 */
export async function startDaemonUntil(spec: Omit<DaemonSpec, 'launch'>, launch: Launch, ready: () => Promise<string | undefined>): Promise<LiveDaemon> {
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
  const ended = new Promise<number | string>((resolve) => {
    child.once('exit', (code, signal) => {
      exited = code ?? signal ?? 'unknown';
      resolve(exited);
    });
    child.once('error', (error) => {
      exited = error.message;
      resolve(exited);
    });
  });
  child.unref();
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (exited !== null) throw new Error(`${spec.name} exited early (${exited}):\n${logTail(spec.logPath)}`);
    const url = await ready();
    if (url !== undefined) return { name: spec.name, pid: child.pid as number, url, logPath: spec.logPath, exited: ended, group: spec.group };
    await sleep(POLL_MS);
  }
  await stopDaemon({ pid: child.pid as number, group: spec.group, exited: ended });
  throw new Error(`${spec.name} was not ready within ${START_TIMEOUT_MS / 1000}s:\n${logTail(spec.logPath)}`);
}

/**
 * Starts a daemon on `port`, or on a port the system gives it when there is none, and returns once it publishes its
 * URL to its listening file. The file is deleted first, so one left by an earlier run is never read as this start's.
 * A pinned `port` is a URL the daemon held before; whatever holds it now fails the start.
 */
export function startDaemon(spec: DaemonSpec, port = 0): Promise<LiveDaemon> {
  const file = listeningFile(spec);
  fs.rmSync(file, { force: true });
  return startDaemonUntil(spec, spec.launch(port, file), async () => published(file));
}

/** Where a daemon publishes its URL: beside its log, one per log. */
export function listeningFile(spec: DaemonSpec): string {
  return `${spec.logPath}.listening`;
}

/** `SIGTERM`, then `SIGKILL` after the grace; resolves with how the daemon exited. */
export async function stopLive(daemon: LiveDaemon): Promise<number | string> {
  if (!isAlive(daemon.pid)) return daemon.exited;
  signalDaemon(daemon, 'SIGTERM');
  const grace = sleep(STOP_GRACE_MS).then(() => undefined);
  if ((await Promise.race([daemon.exited, grace])) === undefined) await killLive(daemon);
  return daemon.exited;
}

/** `SIGKILL`; resolves with how the daemon exited. */
export async function killLive(daemon: LiveDaemon): Promise<number | string> {
  try {
    signalDaemon(daemon, 'SIGKILL');
  } catch {
    // It exited before the signal.
  }
  return daemon.exited;
}

/**
 * `SIGTERM`, then `SIGKILL` if the daemon has not exited ten seconds later. A daemon this process started carries its
 * `exited` promise and is waited on through it; a bare record, which another process started, is polled.
 */
export async function stopDaemon(daemon: { pid: number; group?: boolean; exited?: Promise<number | string> }): Promise<void> {
  if (!isAlive(daemon.pid)) {
    // A dead leader can leave workers in its group; reap them.
    if (daemon.group === true) {
      try {
        signalDaemon(daemon, 'SIGKILL');
      } catch {
        // The group is empty.
      }
    }
    return;
  }
  signalDaemon(daemon, 'SIGTERM');
  if (daemon.exited !== undefined) {
    const grace = sleep(STOP_GRACE_MS).then(() => undefined);
    if ((await Promise.race([daemon.exited, grace])) !== undefined) return;
  } else {
    const deadline = Date.now() + STOP_GRACE_MS;
    while (Date.now() < deadline) {
      if (!isAlive(daemon.pid)) return;
      await sleep(POLL_MS);
    }
  }
  try {
    signalDaemon(daemon, 'SIGKILL');
  } catch {
    // It exited between the last check and the kill.
  }
}
