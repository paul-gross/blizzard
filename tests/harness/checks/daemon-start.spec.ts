import { expect, test } from '@playwright/test';
import * as http from 'node:http';
import { type DaemonSpec, freePort, isAlive, startDaemon, stopLive } from '../daemons/process';

// A daemon that answers every route on the port it is given, or exits naming the address in use.
const SERVE = [
  "const server = require('node:http').createServer((_, res) => res.end('ok'));",
  "server.on('error', (error) => { console.error(error.message); process.exit(1); });",
  "server.listen(Number(process.argv[1]), '127.0.0.1');",
].join('\n');

function spec(dir: string): DaemonSpec {
  return {
    name: 'answering',
    cwd: dir,
    logPath: `${dir}/answering.log`,
    healthPath: '/health',
    launch: (port) => ({ command: process.execPath, args: ['-e', SERVE, String(port)], env: process.env }),
  };
}

test('another process answering the health route on a daemon’s port does not start it', async () => {
  const port = await freePort();
  const impostor = http.createServer((_, res) => res.end('ok'));
  await new Promise<void>((resolve) => impostor.listen(port, '127.0.0.1', resolve));
  try {
    await expect(startDaemon(spec(test.info().outputPath()), port)).rejects.toThrow(/could not take port/);
  } finally {
    impostor.close();
  }
});

test('a daemon that takes its own port is started on it', async () => {
  const port = await freePort();
  const daemon = await startDaemon(spec(test.info().outputPath()), port);
  try {
    expect(daemon.url).toBe(`http://127.0.0.1:${port}`);
    expect(isAlive(daemon.pid)).toBe(true);
  } finally {
    await stopLive(daemon);
  }
});
