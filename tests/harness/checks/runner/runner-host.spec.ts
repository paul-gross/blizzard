import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { isAlive } from '../../daemons/process';
import { expect, runnerTest as test } from '../../fixtures';
import { runnerHostBuilder } from '../../runner-host';

function groupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch {
    return false;
  }
}

test('a default host boots stepped, and a step completes', async ({ runnerHost }) => {
  const runner = runnerHost.runners['runner-local'];
  expect(runner).toBeDefined();
  // Stepped: nothing has run a pass until the test asks.
  expect((await runner?.status())?.body.last_tick_at).toBeNull();
  const pass = await runnerHost.step();
  expect(pass.outcome).toBe('completed');
  const status = await runner?.status();
  expect(status?.status).toBe(200);
  expect(status?.body.runner_id).toBe(runner?.id);
  expect(status?.body.last_tick_at).not.toBeNull();
  expect(fs.existsSync(runnerHost.socket)).toBe(true);
  expect(isAlive(runnerHost.pid as number)).toBe(true);
  expect((await runnerHost.stepHost()).outcome).toBe('completed');
});

test.describe('a profile that cannot be a host is refused before any process starts', () => {
  const refused: [string, Record<string, unknown>, RegExp][] = [
    ['an unknown key', { nonsense_key: 'x' }, /nonsense_key: not a key the host declares/],
    ['a deprecated key', { workspace_root: '/x' }, /workspace_root: deprecated/],
    ['a mistyped key', { runner: [{ name: 'runner-local', agents: 'two' }] }, /runner\[\]\.agents: declared integer, the profile has string/],
    ['an undeclared placeholder', { runner: [{ name: 'runner-local', runner_prompt_file: '${idp}/x' }] }, /\$\{idp\} is not a placeholder/],
    ['a key the CLI owns', { hub_url: 'http://x' }, /hub_url: the CLI or the fixture owns it/],
    ['a key the fixture owns', { port: 1n }, /port: the CLI or the fixture owns it/],
  ];
  for (const [what, config, message] of refused) {
    test(what, async ({ runnerHosts, mockHub }) => {
      await expect(runnerHosts.create({ runnerConfig: config as never })).rejects.toThrow(message);
      // Nothing enrolled: the mock hub never heard from a host.
      expect((await mockHub.captured.read()).body.requests).toEqual([]);
    });
  }
});

test('a socket path over the limit is refused before init, naming the path and the limit', async ({ mockHub, worldContext, ledger, vendorUsage }, testInfo) => {
  const scratchRoot = path.join(os.tmpdir(), 'x'.repeat(120));
  const builder = runnerHostBuilder({ hub: { url: mockHub.url }, context: worldContext, ledger, testInfo, vendor: vendorUsage, scratchRoot });
  await expect(builder.create()).rejects.toThrow(/host\.sock is \d+ bytes, over the 107-byte limit/);
  expect(fs.existsSync(scratchRoot)).toBe(false);
  expect((await mockHub.captured.read()).body.requests).toEqual([]);
});

test('a setup that fails after the host process spawned leaves no process and no scratch root', async ({ mockHub, worldContext, ledger, vendorUsage }, testInfo) => {
  const scratchRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bzh-check-'));
  try {
    const builder = runnerHostBuilder({ hub: { url: mockHub.url }, context: worldContext, ledger, testInfo, vendor: vendorUsage, scratchRoot });
    // A workspace provider the host does not know: the process starts, and exits before it answers.
    const config = { workspace: [{ id: 'workspace-local', provider: 'no-such-provider' }] };
    await expect(builder.create({ runnerConfig: config })).rejects.toThrow(/exited early|did not answer/);
    expect(fs.readdirSync(scratchRoot)).toEqual([]);
    await builder.disposeAll();
  } finally {
    fs.rmSync(scratchRoot, { recursive: true, force: true });
  }
});

test.describe('independent worlds', () => {
  test.describe.configure({ mode: 'serial' });
  let first: { dir: string; url: string; hub: string } | undefined;

  test('the first test’s host', async ({ runnerHost, mockHub }) => {
    first = { dir: runnerHost.dir, url: runnerHost.url, hub: mockHub.url };
    expect((await runnerHost.api.runners()).ok).toBe(true);
  });

  test('the second test’s host shares nothing with it', async ({ runnerHost, mockHub }) => {
    expect(first).toBeDefined();
    expect(runnerHost.dir).not.toBe(first?.dir);
    expect(runnerHost.url).not.toBe(first?.url);
    expect(mockHub.url).not.toBe(first?.hub);
    expect(fs.existsSync(first?.dir as string)).toBe(false);
  });
});

test.describe('a profile naming a second runner', () => {
  test.use({ runnerConfig: { runner: [{ name: 'runner-two', projects: [{ project: 'default', workspace: 'workspace-local' }] }] } });

  test('gets it through add; both enroll at the test’s mock hub, each steps by name, and step() alone refuses', async ({ runnerHost }) => {
    expect(Object.keys(runnerHost.runners).sort()).toEqual(['runner-local', 'runner-two']);
    const [one, two] = [runnerHost.runners['runner-local'], runnerHost.runners['runner-two']];
    expect(one?.id).not.toBe(two?.id);
    for (const name of ['runner-local', 'runner-two']) expect((await runnerHost.step(name)).outcome).toBe('completed');
    expect((await one?.status())?.body.last_tick_at).not.toBeNull();
    expect((await two?.status())?.body.last_tick_at).not.toBeNull();
    expect(() => runnerHost.step()).toThrow(/needs a runner name/);
  });
});

test('stop and kill end the host’s whole process group', async ({ runnerHosts }) => {
  const stopped = await runnerHosts.create();
  const stoppedPid = stopped.pid as number;
  await stopped.stop();
  expect(groupAlive(stoppedPid)).toBe(false);
  const killed = await runnerHosts.create();
  const killedPid = killed.pid as number;
  await killed.kill();
  expect(groupAlive(killedPid)).toBe(false);
});

test('restart comes back on the same directory and address with a new pid', async ({ runnerHost }) => {
  const before = { dir: runnerHost.dir, url: runnerHost.url, pid: runnerHost.pid, id: runnerHost.runners['runner-local']?.id };
  await runnerHost.restart();
  expect(runnerHost.pid).not.toBe(before.pid);
  expect(runnerHost.dir).toBe(before.dir);
  expect(runnerHost.url).toBe(before.url);
  expect((await runnerHost.runners['runner-local']?.status())?.body.runner_id).toBe(before.id);
  expect((await runnerHost.step()).outcome).toBe('completed');
});

test.describe('an unstarted host', () => {
  test.use({ runnerStarted: false });

  test('has its files and no process; restart() is its first boot', async ({ runnerHost }) => {
    expect(runnerHost.pid).toBeUndefined();
    for (const name of ['blizzard-host.toml', '.env', 'data']) expect(fs.existsSync(path.join(runnerHost.dir, name))).toBe(true);
    await runnerHost.restart();
    expect(runnerHost.pid).toBeDefined();
    expect((await runnerHost.step()).outcome).toBe('completed');
  });

  test('prepares documented files before boot and refuses live changes', async ({ runnerHost }) => {
    const name = 'runner-local';
    runnerHost.prepareStopped({ runnerSubscriptions: { [name]: 'missing' } });
    await runnerHost.restart();
    expect((await runnerHost.api.health()).status).toBe(200);
    expect((await runnerHost.api.ready()).body.detail).toContain("names subscription 'missing'");
    expect((await runnerHost.runners[name]?.expecting(503).status())?.status).toBe(503);
    expect(() => runnerHost.prepareStopped({ runnerSubscriptions: { [name]: 'anthropic' } })).toThrow(/stop the host/);
    await runnerHost.stop();
    runnerHost.prepareStopped({ runnerSubscriptions: { [name]: 'anthropic' } });
    await runnerHost.restart();
    expect((await runnerHost.runners[name]?.status())?.status).toBe(200);
  });

  test('rejects unknown runner names and carries enrolled tokens and an explicit id into first boot', async ({ runnerHost, mockHub }) => {
    const name = 'runner-local';
    expect(() => runnerHost.prepareStopped({ runnerIds: { absent: 'rn_wrong' } })).toThrow(/no runner absent/);
    const enrolled = (await mockHub.seed.runner({ name: 'standalone' })).body;
    runnerHost.prepareStopped({ runnerIds: { [name]: enrolled.runner_id }, runnerTokens: { [name]: enrolled.token } });
    expect(runnerHost.runners[name]?.id).toBe(enrolled.runner_id);
    expect(fs.readFileSync(path.join(runnerHost.dir, '.env'), 'utf8')).toContain(`BZ_HUB_TOKEN_RUNNER_LOCAL=${enrolled.token}`);
    await runnerHost.restart();
    expect((await runnerHost.api.health()).status).toBe(200);
    expect((await runnerHost.api.ready()).body.detail).toContain(enrolled.runner_id);
    expect((await runnerHost.runners[name]?.expecting(503).status())?.status).toBe(503);
  });
});

test.describe('a timed host', () => {
  test.use({ runnerLoop: 'timed', runnerEnv: { BZ_RUNNER_TICK_SECONDS: '1' } });

  test('completes a pass without a step', async ({ runnerHost }) => {
    const runner = runnerHost.runners['runner-local'];
    await expect.poll(async () => (await runner?.status())?.body.last_tick_at, { timeout: 30_000 }).not.toBeNull();
  });
});

test('a step during a pass in progress is rejected with the host log attached', async ({ runnerHost }) => {
  const settled = await Promise.allSettled(Array.from({ length: 8 }, () => runnerHost.step()));
  const rejected = settled.filter((result): result is PromiseRejectedResult => result.status === 'rejected');
  expect(settled.some((result) => result.status === 'fulfilled')).toBe(true);
  expect(rejected.length).toBeGreaterThan(0);
  expect(String(rejected[0]?.reason)).toMatch(/answered 409.*tick_in_progress[\s\S]*host log:/);
});

test.describe('no ambient credentials', () => {
  const ambient = { BZ_HOST_TOKEN: 'ambient-host', BZ_HUB_TOKEN_OTHER: 'ambient-hub', CLAUDE_CONFIG_DIR: '/ambient/claude', CODEX_HOME: '/ambient/codex', BZ_WORKSPACE_ROOT: '/ambient/ws' };

  test('the host process carries none of the developer’s, and a default stepHost sends no vendor request', async ({ runnerHosts, vendorUsage }) => {
    // The developer's environment, as the worker has it, for the span of the host's build.
    const held = Object.fromEntries(Object.keys(ambient).map((name) => [name, process.env[name]]));
    Object.assign(process.env, ambient);
    let host;
    try {
      host = await runnerHosts.create();
    } finally {
      for (const [name, value] of Object.entries(held)) {
        if (value === undefined) delete process.env[name];
        else process.env[name] = value;
      }
    }
    const environ = Object.fromEntries(
      fs
        .readFileSync(`/proc/${host.pid}/environ`, 'utf8')
        .split('\0')
        .filter((entry) => entry.includes('='))
        .map((entry) => [entry.slice(0, entry.indexOf('=')), entry.slice(entry.indexOf('=') + 1)]),
    );
    expect(environ.HOME).toBe(host.home);
    expect(environ.CLAUDE_CONFIG_DIR?.startsWith(host.home)).toBe(true);
    expect(environ.CODEX_HOME?.startsWith(host.home)).toBe(true);
    expect(environ.XDG_CONFIG_HOME?.startsWith(host.home)).toBe(true);
    expect(environ.BLIZZARD_MOCK_HARNESS_FENCE).toBe('1');
    expect(Object.keys(environ).filter((name) => /^BZ_(HOST_TOKEN|HUB_TOKEN|WORKSPACE_ROOT)/.test(name))).toEqual([]);
    expect((await host.stepHost()).outcome).toBe('completed');
    expect(vendorUsage.requests).toEqual([]);
  });

  test('runnerEnv naming a pinned variable or the fence is refused', async ({ runnerHosts }) => {
    await expect(runnerHosts.create({ runnerEnv: { BZ_LOG_FORMAT: 'text' } })).rejects.toThrow(/runnerEnv BZ_LOG_FORMAT: .*sets it/);
    await expect(runnerHosts.create({ runnerEnv: { BLIZZARD_MOCK_HARNESS_FENCE: '0' } })).rejects.toThrow(/runnerEnv BLIZZARD_MOCK_HARNESS_FENCE/);
  });
});
