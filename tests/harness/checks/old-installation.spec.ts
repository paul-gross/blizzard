import { expect, oldInstallationTest as test } from '../fixtures';
import * as fs from 'node:fs';

test.setTimeout(300_000);

test('old installation owns a migrated hub and an enrolled real release runner', async ({ oldInstallation }) => {
  const { api, runnerId, runnerPid, runnerBin, tag } = oldInstallation;
  expect(tag).toMatch(/^v/);
  expect(fs.realpathSync(runnerBin)).toContain('old-installation/venv/');
  expect(() => process.kill(runnerPid, 0)).not.toThrow();
  expect((await api.get('/api/ready')).status).toBe(200);
  const projects = await api.get('/api/projects');
  expect(projects.body.projects).toEqual(expect.arrayContaining([expect.objectContaining({ slug: 'rehearsal' })]));
  const runners = await api.get('/api/runners');
  expect((runners.body.runners as { runner_id: string }[]).map((runner: { runner_id: string }) => runner.runner_id)).toContain(runnerId);
  await oldInstallation.close();
  expect(() => process.kill(runnerPid, 0)).toThrow();
});

test('forced shutdown kills only the owned process tree', async () => {
  const { spawn } = await import('node:child_process');
  const readline = await import('node:readline');
  const { stopOwnedProcess } = await import('../owned-process');
  const child = spawn(process.execPath, ['-e', `
    const { spawn } = require('node:child_process');
    const nested = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: true, stdio: 'ignore' });
    process.stdout.write(String(nested.pid) + '\\n');
    process.stdin.resume();
    setInterval(() => {}, 1000);
  `], { stdio: 'pipe' });
  const lines = readline.createInterface({ input: child.stdout });
  const line = await lines[Symbol.asyncIterator]().next();
  const descendant = Number(line.value);
  expect(() => process.kill(descendant, 0)).not.toThrow();
  expect(await stopOwnedProcess(child, 100)).toBe(false);
  expect(child.signalCode).toBe('SIGKILL');
  // An orphan may briefly remain as a zombie until its new parent reaps it.
  await expect.poll(() => {
    try { return fs.readFileSync(`/proc/${descendant}/stat`, 'utf8').split(') ')[1]?.startsWith('Z'); }
    catch { return true; }
  }).toBe(true);
  expect(() => process.kill(process.pid, 0)).not.toThrow();
});
