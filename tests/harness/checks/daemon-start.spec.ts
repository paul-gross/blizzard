import { expect, test } from '@playwright/test';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { type DaemonSpec, isAlive, listeningFile, startDaemon, stopLive } from '../daemons/process';

// A daemon that binds the port it is given, `0` for one the system picks, and publishes its URL to the listening file
// as the blizzard daemons do: written whole, renamed into place, once it serves.
const SERVE = [
  "const fs = require('node:fs');",
  "const server = require('node:http').createServer((_, res) => res.end('ok'));",
  "server.on('error', (error) => { console.error(error.message); process.exit(1); });",
  "server.listen(Number(process.argv[1]), '127.0.0.1', () => {",
  "  const file = process.argv[2];",
  "  fs.writeFileSync(`${file}.tmp`, `http://127.0.0.1:${server.address().port}\\n`);",
  "  fs.renameSync(`${file}.tmp`, file);",
  '});',
].join('\n');

function spec(dir: string): DaemonSpec {
  return {
    name: 'answering',
    cwd: dir,
    logPath: path.join(dir, 'answering.log'),
    launch: (port, file) => ({ command: process.execPath, args: ['-e', SERVE, String(port), file], env: process.env }),
  };
}

/** The body `url` answers with, read over a bare socket: the daemon under test is no blizzard service. */
function body(url: string): Promise<string> {
  return new Promise((resolve, reject) => {
    http.get(url, (response) => {
      let text = '';
      response.setEncoding('utf8');
      response.on('data', (chunk: string) => (text += chunk));
      response.on('end', () => resolve(text));
    }).on('error', reject);
  });
}

test('a daemon started on port 0 publishes the URL it answers at', async ({}, testInfo) => {
  const daemon = await startDaemon(spec(testInfo.outputPath()));
  try {
    expect(daemon.url).toMatch(/^http:\/\/127\.0\.0\.1:[1-9]\d*$/);
    expect(isAlive(daemon.pid)).toBe(true);
    expect(await body(daemon.url)).toBe('ok');
  } finally {
    await stopLive(daemon);
  }
});

test('a restart pinned to the port its first start published answers at the same URL', async ({}, testInfo) => {
  const first = await startDaemon(spec(testInfo.outputPath()));
  await stopLive(first);
  const again = await startDaemon(spec(testInfo.outputPath()), Number(new URL(first.url).port));
  try {
    expect(again.url).toBe(first.url);
    expect(await body(again.url)).toBe('ok');
  } finally {
    await stopLive(again);
  }
});

test('a daemon that exits before publishing fails its start with its log tail', async ({}, testInfo) => {
  const dying: DaemonSpec = {
    ...spec(testInfo.outputPath()),
    launch: () => ({ command: process.execPath, args: ['-e', "console.error('refusing to serve'); process.exit(3)"], env: process.env }),
  };
  await expect(startDaemon(dying)).rejects.toThrow(/exited early \(3\):[\s\S]*refusing to serve/);
});

test('a listening file left by an earlier run is not read as this start’s readiness', async ({}, testInfo) => {
  test.setTimeout(60_000);
  const dir = testInfo.outputPath();
  const pidFile = path.join(dir, 'pid');
  const silent: DaemonSpec = {
    ...spec(dir),
    launch: () => ({
      command: process.execPath,
      args: ['-e', "require('node:fs').writeFileSync(process.argv[1], String(process.pid)); setInterval(() => {}, 1000)", pidFile],
      env: process.env,
    }),
  };
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(listeningFile(silent), 'http://127.0.0.1:1\n');
  await expect(startDaemon(silent)).rejects.toThrow(/was not ready/);
  // The start timed out rather than reading the stale URL, and stopped what it spawned.
  const pid = Number(fs.readFileSync(pidFile, 'utf8'));
  expect(pid).toBeGreaterThan(0);
  expect(isAlive(pid)).toBe(false);
});
