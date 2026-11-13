import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import * as path from 'node:path';
import * as readline from 'node:readline';
import type { PlaywrightWorkerArgs, TestInfo } from '@playwright/test';
type Playwright = PlaywrightWorkerArgs['playwright'];
import { BLIZZARD_ROOT, binDir, daemonEnv } from './env';
import { hubApi, type HubApi } from './client/clients';
import { Ledger } from './client/ledger';
import { stopOwnedProcess } from './owned-process';

export interface OldInstallation {
  api: HubApi;
  tag: string;
  runnerId: string;
  runnerPid: number;
  runnerBin: string;
  ingest(title: string): Promise<string>;
  close(): Promise<void>;
}

interface Ready { url: string; tag: string; runnerId: string; runnerPid: number; runnerBin: string }

/** A release's real hub store migrated to this checkout, with its own release runner enrolled and running. */
export async function startOldInstallation(root: string, playwright: Playwright, testInfo: TestInfo): Promise<OldInstallation> {
  const child: ChildProcessWithoutNullStreams = spawn(path.join(binDir(), 'python'), [
    '-m', 'tests.harness.old_installation', root, binDir(),
  ], { cwd: BLIZZARD_ROOT, env: daemonEnv(), stdio: 'pipe' });
  let stderr = '';
  child.stderr.setEncoding('utf8').on('data', (chunk: string) => { stderr += chunk; });
  const lines = readline.createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  const next = async (): Promise<Record<string, unknown>> => {
    const line = await lines.next();
    if (line.done) throw new Error(`old installation exited (${child.exitCode}):\n${stderr}`);
    return JSON.parse(line.value) as Record<string, unknown>;
  };
  try {
    const ready = await next() as unknown as Ready;
    const context = await playwright.request.newContext({ baseURL: ready.url });
    const api = hubApi({ context, testInfo, narrow: false, level: 'fresh-hub', clockMode: 'system', ledger: new Ledger(), logContext: () => stderr });
    let closed = false;
    return {
      ...ready, api,
      ingest: async (title) => {
        child.stdin.write(JSON.stringify({ op: 'ingest', title }) + '\n');
        return (await next()).chunkId as string;
      },
      close: async () => {
        if (closed) return;
        closed = true;
        child.stdin.end(JSON.stringify({ op: 'close' }) + '\n');
        const graceful = await stopOwnedProcess(child);
        await context.dispose();
        if (!graceful) throw new Error(`old installation shutdown timed out:\n${stderr}`);
        if (child.exitCode !== 0) throw new Error(`old installation exited (${child.exitCode}):\n${stderr}`);
      },
    };
  } catch (error) {
    child.stdin.end();
    await stopOwnedProcess(child);
    throw error;
  }
}
