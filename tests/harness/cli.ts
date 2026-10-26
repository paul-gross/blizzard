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

/** A prompt a command stops at: once its output matches `until`, `reply` answers and the answer is written to stdin. */
export interface Prompt {
  until: RegExp;
  reply: (match: RegExpExecArray) => Promise<string>;
}

/**
 * Runs `command args` to completion and returns its exit code and output; a non-zero exit is a result, not an error.
 * A command still running after `timeoutMs` is killed with everything it started, and the call throws naming it. With a
 * `prompt` the command's stdin is answered once; without one it is closed at once.
 */
export function runCommand(
  command: string,
  args: string[],
  options: { env: NodeJS.ProcessEnv; cwd?: string; timeoutMs?: number; prompt?: Prompt },
): Promise<CliResult> {
  const timeoutMs = options.timeoutMs ?? CLI_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    // Detached, so the kill reaches the whole process group and not only a wrapper shell.
    const child = spawn(command, args, { env: options.env, cwd: options.cwd, stdio: 'pipe', detached: true });
    // Without a prompt the command reads end-of-input at once, as it would from a closed stdin.
    if (options.prompt === undefined) child.stdin.end();
    // A command that exits before it is answered closes the pipe; the exit is the result, not a write error.
    child.stdin.on('error', () => {});
    let stdout = '';
    let stderr = '';
    let asked = false;
    const kill = (): void => {
      try {
        process.kill(-(child.pid as number), 'SIGKILL');
      } catch {
        // It exited between the timer and the kill.
      }
    };
    const timer = setTimeout(() => {
      kill();
      reject(new CliTimeout(`\`${[command, ...args].join(' ')}\` did not exit within ${timeoutMs / 1000}s`, stdout, stderr));
    }, timeoutMs);
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => {
      stdout += chunk;
      const prompt = options.prompt;
      if (prompt === undefined || asked) return;
      const match = prompt.until.exec(stdout);
      if (match === null) return;
      asked = true;
      prompt.reply(match).then(
        (answer) => child.stdin.end(`${answer}\n`),
        (error: unknown) => {
          kill();
          clearTimeout(timer);
          reject(error);
        },
      );
    });
    child.stderr.setEncoding('utf8').on('data', (chunk: string) => (stderr += chunk));
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
