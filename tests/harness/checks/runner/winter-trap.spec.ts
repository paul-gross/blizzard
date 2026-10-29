import * as fs from 'node:fs';
import * as path from 'node:path';
import { runCommand } from '../../cli';
import { expect, runnerTest as test } from '../../fixtures';
import type { RunnerHost } from '../../fixtures';

/** The `PATH` of the host's running process, as the process itself holds it. */
function processPath(host: RunnerHost): string {
  const environ = fs.readFileSync(`/proc/${host.pid}/environ`, 'utf8').split('\0');
  return environ.find((entry) => entry.startsWith('PATH='))?.slice('PATH='.length) ?? '';
}

test.describe('with runnerWinterTrap', () => {
  test.use({ runnerWinterTrap: true });

  test('the trap is first on the host’s PATH, and running it is reported', async ({ runnerHost }) => {
    const entries = processPath(runnerHost).split(path.delimiter);
    expect(fs.existsSync(path.join(entries[0] as string, 'winter'))).toBe(true);
    expect(entries.length).toBeGreaterThan(1);
    expect(runnerHost.winterInvoked()).toBe(false);

    const result = await runCommand('winter', [], { env: { PATH: entries.join(path.delimiter) } });
    expect(result.code).toBe(99);
    expect(runnerHost.winterInvoked()).toBe(true);
  });

  test('a host the test makes beside its own carries the trap too', async ({ runnerHosts }) => {
    const second = await runnerHosts.create();
    expect(fs.existsSync(path.join(processPath(second).split(path.delimiter)[0] as string, 'winter'))).toBe(true);
    expect(second.winterInvoked()).toBe(false);
  });

  test('a runnerEnv.PATH beside the trap is refused', async ({ runnerHosts }) => {
    await expect(runnerHosts.create({ runnerEnv: { PATH: '/usr/bin' } })).rejects.toThrow(/runnerEnv PATH/);
  });
});

test('a host without the option has the ambient PATH and no trap', async ({ runnerHost }) => {
  const entries = processPath(runnerHost).split(path.delimiter);
  expect(entries.join(path.delimiter)).toBe(process.env.PATH);
  expect(entries.some((entry) => fs.existsSync(path.join(entry, 'winter-invoked')))).toBe(false);
  expect(runnerHost.winterInvoked()).toBe(false);
});
