import { expect, test } from '../fixtures';
import { isAlive } from '../daemons/process';
import { withSecondForge } from '../second-forge';

test.use({ isolation: 'fresh-tenant' });

test.describe('the second forge lifecycle', () => {
  test.describe.configure({ mode: 'serial' });
  let firstPid: number;

  test('the second forge owns distinct repositories and origins', async ({ forge, secondForge }) => {
    firstPid = secondForge.pid;
    expect(secondForge.url).not.toBe(forge.url);
    const first = await forge.createRepository('fixture', 'first-forge');
    const second = await secondForge.createRepository('fixture', 'second-forge');
    expect(first.origin).not.toBe(second.origin);
    expect(await forge.originLog('fixture/first-forge', 'main')).toBeTruthy();
    expect(await secondForge.originLog('fixture/second-forge', 'main')).toBeTruthy();
    expect(await forge.originLog('fixture/second-forge', 'main')).toBeUndefined();
    expect(await secondForge.originLog('fixture/first-forge', 'main')).toBeUndefined();
  });

  test('the preceding forge is stopped and the next test gets a fresh forge', async ({ secondForge }) => {
    expect(secondForge.pid).not.toBe(firstPid);
    expect(isAlive(firstPid)).toBe(false);
    expect(await secondForge.originLog('fixture/second-forge', 'main')).toBeUndefined();
  });
});

test('a second forge is stopped when client setup fails after startup', async ({}, testInfo) => {
  let pid = 0;
  await expect(withSecondForge(testInfo.outputPath('failed-forge'), async (daemon) => {
    pid = daemon.pid;
    expect(isAlive(pid)).toBe(true);
    throw new Error('client setup failed');
  })).rejects.toThrow('client setup failed');
  expect(pid).not.toBe(0);
  expect(isAlive(pid)).toBe(false);
});
