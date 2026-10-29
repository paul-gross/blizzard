import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { expect, runnerTest as test } from '../../fixtures';
import { scratchDirs, startPreHostDaemon } from '../../scratch-dirs';

test('a scratch root over the socket limit is refused, naming the socket path and the limit', () => {
  const scratchRoot = path.join(os.tmpdir(), 'x'.repeat(120));
  const dirs = scratchDirs({ scratchRoot });
  expect(() => dirs.make('d')).toThrow(/runner\.sock is \d+ bytes, over the 107-byte limit/);
  expect(fs.existsSync(scratchRoot)).toBe(false);
});

test.describe('the scratch directories fixture', () => {
  test.describe.configure({ mode: 'serial' });
  let made: string | undefined;

  test('runs the CLI on a directory with no host and no mock hub, and makes only what it is asked', async ({ scratchDirs: dirs }) => {
    made = dirs.make('subject');
    expect(fs.readdirSync(made)).toEqual([]);
    const done = await dirs.cli(['host', '--dir', made, '--check']);
    expect(done.code).not.toBe(0);
    expect(done.stdout + done.stderr).not.toBe('');
  });

  test('has removed the directory by the time the next test runs', () => {
    expect(made).toBeDefined();
    expect(fs.existsSync(made as string)).toBe(false);
  });
});

test('the pre-host daemon answers 200 on its socket, and the socket is gone after close', async ({ scratchDirs: dirs }) => {
  const dir = dirs.make('daemon');
  const daemon = await startPreHostDaemon(dir);
  expect(daemon.socket).toBe(path.join(dir, 'runner.sock'));
  const answer = await new Promise<{ status: number | undefined; body: string }>((resolve, reject) => {
    const request = http.get({ socketPath: daemon.socket, path: '/api/health' }, (response) => {
      let body = '';
      response.setEncoding('utf8').on('data', (chunk: string) => (body += chunk));
      response.on('end', () => resolve({ status: response.statusCode, body }));
    });
    request.on('error', reject);
  });
  expect(answer).toEqual({ status: 200, body: '{}' });
  await daemon.close();
  expect(fs.existsSync(daemon.socket)).toBe(false);
});
