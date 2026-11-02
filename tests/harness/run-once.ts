import * as fs from 'node:fs';
import * as path from 'node:path';
import { runDir } from './env';
import { sleep } from './daemons/process';

const DEFAULT_WAIT_MS = 120_000;

/** Writes `body` to `file` whole: under a temporary name, then renamed into place, so a waiter that sees the file never reads it half-written. */
export function publish(file: string, body: string): void {
  const partial = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.partial`;
  fs.writeFileSync(partial, body);
  fs.renameSync(partial, file);
}

/**
 * Runs `build` once per run directory, whichever process asks first: the one that creates `<name>.lock` builds and
 * writes `<name>.done`, and every other waits on that file. A build that throws is published, so each waiter raises the
 * same error rather than timing out. `dir` defaults to this run's directory.
 */
export async function runOnce(name: string, build: () => Promise<void> | void, options: { dir?: string; waitMs?: number } = {}): Promise<void> {
  const dir = options.dir ?? runDir();
  fs.mkdirSync(dir, { recursive: true });
  const lock = path.join(dir, `${name}.lock`);
  const done = path.join(dir, `${name}.done`);
  try {
    fs.closeSync(fs.openSync(lock, 'wx'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    const deadline = Date.now() + (options.waitMs ?? DEFAULT_WAIT_MS);
    while (!fs.existsSync(done)) {
      if (Date.now() > deadline) throw new Error(`the first process never finished ${name}`, { cause: error });
      await sleep(100);
    }
    const outcome = JSON.parse(fs.readFileSync(done, 'utf8')) as { error?: string };
    if (outcome.error !== undefined) throw new Error(outcome.error, { cause: error });
    return;
  }
  try {
    await build();
    publish(done, JSON.stringify({}));
  } catch (error) {
    publish(done, JSON.stringify({ error: (error as Error).message }));
    throw error;
  }
}
