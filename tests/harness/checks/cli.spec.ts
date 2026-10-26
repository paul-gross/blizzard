import * as fs from 'node:fs';
import { test as plain } from '@playwright/test';
import { runCommand } from '../cli';
import { expect, test } from '../fixtures';

test.describe('the cli fixture', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('lists the chunk the test ingested, through the tenant header alone', async ({ cli, issue }) => {
    const filed = await issue('seen by the cli');
    const listed = await cli(['hub', 'chunk', 'list', '--json']);
    expect(listed.code, listed.stderr).toBe(0);
    expect(listed.stdout).toContain(filed.chunk.chunk_id);
  });

  test('starts with an empty config home of its own', async ({ cli }, testInfo) => {
    const home = testInfo.outputPath('cli-config-home');
    await cli(['--help']);
    expect(fs.existsSync(home)).toBe(true);
    expect(fs.readdirSync(home).filter((entry) => !entry.startsWith('.'))).toEqual([]);
  });

  test('a non-zero exit is returned, not thrown', async ({ cli }) => {
    const result = await cli(['hub', 'chunk', 'no-such-verb']);
    expect(result.code).not.toBe(0);
  });
});

plain('a command that never exits fails at the bound, naming itself', async () => {
  const started = Date.now();
  await expect(runCommand('sh', ['-c', 'sleep 60'], { env: process.env, timeoutMs: 300 })).rejects.toThrow(/`sh -c sleep 60` did not exit within 0\.3s/);
  expect(Date.now() - started).toBeLessThan(5_000);
});
