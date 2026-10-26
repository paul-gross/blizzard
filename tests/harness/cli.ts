import { spawn } from 'node:child_process';

export const CLI_TIMEOUT_MS = 30_000;

export interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

/** A command that outlived its bound; what it had printed by then rides along. */
export class CliTimeout extends Error {
  constructor(
    message: string,
    readonly stdout: string,
    readonly stderr: string,
  ) {
    super(message);
  }
}

/**
 * Runs `command args` to completion and returns its exit code and output; a non-zero exit is a result, not an error.
 * A command still running after `timeoutMs` is killed with everything it started, and the call throws naming it.
 */
export function runCommand(
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; cwd?: string; timeoutMs?: number },
): Promise<CliResult> {
  const timeoutMs = options.timeoutMs ?? CLI_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    // Detached, so the kill reaches the whole process group and not only a wrapper shell.
    const child = spawn(command, args, { env: options.env, cwd: options.cwd, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => (stdout += chunk));
    child.stderr.setEncoding('utf8').on('data', (chunk: string) => (stderr += chunk));
    const timer = setTimeout(() => {
      try {
        process.kill(-(child.pid as number), 'SIGKILL');
      } catch {
        // It exited between the timer and the kill.
      }
      reject(new CliTimeout(`\`${[command, ...args].join(' ')}\` did not exit within ${timeoutMs / 1000}s`, stdout, stderr));
    }, timeoutMs);
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      resolve({ code: code ?? (signal === null ? -1 : 128), stdout, stderr });
    });
  });
}
