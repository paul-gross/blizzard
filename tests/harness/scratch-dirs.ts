import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { type CliResult, runCommand } from './cli';
import { binDir, requireBinary } from './env';
import { isolatedEnv, socketPathProblem } from './runner-host';

/** Directories a test makes for `blizzard-runner` verbs to run on with no host serving them, and the CLI that runs there. */
export interface ScratchDirs {
  /** A new empty directory named `name`; refuses when its `runner.sock` would be over the socket path limit. */
  make(name: string): string;
  /** The real `blizzard-runner` under the isolated environment, in the scratch root. A non-zero exit is the result, not an error. */
  cli(args: string[]): Promise<CliResult>;
  /** Removes every directory made. */
  dispose(): void;
}

/** Scratch directories under the root a runner host builder uses; `scratchRoot` is overridden by the harness check alone. */
export function scratchDirs(options: { scratchRoot?: string } = {}): ScratchDirs {
  const root = path.join(options.scratchRoot ?? os.tmpdir(), `bzs-${crypto.randomBytes(4).toString('hex')}`);
  const home = path.join(root, 'home');
  return {
    make(name) {
      const dir = path.join(root, name);
      const tooLong = socketPathProblem(path.join(dir, 'runner.sock'));
      if (tooLong !== undefined) throw new Error(tooLong);
      fs.mkdirSync(dir, { recursive: true });
      return dir;
    },
    cli(args) {
      fs.mkdirSync(home, { recursive: true });
      const env = isolatedEnv(home, path.join(home, '.config'), {});
      return runCommand(requireBinary(binDir(), 'blizzard-runner'), args, {
        env,
        cwd: root,
      });
    },
    dispose() {
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}

/**
 * A listener on `<dir>/runner.sock` answering every request `200 {}`: a pre-host daemon still serving the directory.
 * Resolves once it is listening, with the socket path; `close` stops it and removes the socket.
 */
export async function startPreHostDaemon(dir: string): Promise<{ socket: string; close(): Promise<void> }> {
  const socket = path.join(dir, 'runner.sock');
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end('{}');
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(socket, resolve);
  });
  return {
    socket,
    async close() {
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
      fs.rmSync(socket, { force: true });
    },
  };
}
