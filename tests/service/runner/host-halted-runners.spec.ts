import { expect, runnerTest as test, type RunnerHost, type MockHub } from '../../harness/fixtures';

test.setTimeout(90_000);

const second = { name: 'runner-b', projects: [{ project: 'p-b', workspace: 'workspace-local' }] };
const third = { name: 'runner-c', projects: [{ project: 'p-c', workspace: 'workspace-local' }] };
const chunk = {
  entry: 'build',
  nodes: {
    build: { executor: 'runner', session: 'resume', judged_by: 'worker', prompt: "print('worked')\n", judgement_prompt: "verdict('pass', 'worked')\n", choices: [{ name: 'pass', to: 'deliver', description: 'worked' }] },
    deliver: { executor: 'hub', judgement: { choices: { landed: { description: 'done', to: 'done' } } } },
  },
};

async function seedProject(hub: MockHub, slug: string): Promise<string> {
  const added = await hub.seed.project({ slug, name: slug });
  expect(added.status).toBe(201);
  return added.body.project_id;
}

async function seedChunk(hub: MockHub, projectId?: string): Promise<string> {
  const added = await hub.seed.chunk({ ...chunk, ...(projectId === undefined ? {} : { project_id: projectId }) });
  expect(added.status).toBe(201);
  return added.body.chunk_id;
}

async function passesUntil(host: RunnerHost, name: string, read: () => Promise<boolean>): Promise<void> {
  for (let pass = 0; pass < 40; pass++) {
    if (await read()) return;
    expect((await host.step(name)).outcome).toBe('completed');
  }
  throw new Error(`runner ${name} did not reach its state after 40 passes:\n${host.logTail()}`);
}

async function leased(host: RunnerHost, name: string, chunkId: string): Promise<boolean> {
  return (await host.runners[name]!.leases()).body.items.some((lease) => lease.chunk_id === chunkId);
}

test('test_a_token_that_resolves_to_another_runner_halts_that_runner_while_the_host_serves', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  const other = (await mockHub.seed.runner({ name: 'runner-other' })).body;
  host.prepareStopped({ runnerTokens: { 'runner-local': other.token } });
  await host.restart();
  await host.step();
  const runner = host.runners['runner-local']!;
  expect((await host.api.health()).status).toBe(200);
  const status = await runner.status();
  expect(status.body.identity?.state).toBe('mismatched');
  const cli = await host.cli(['status', '--dir', host.dir]);
  expect(cli.stdout + cli.stderr).toContain(`REFUSING: token belongs to runner ${other.runner_id}`);
  expect(cli.stdout + cli.stderr).toContain('BZ_HUB_TOKEN_RUNNER_LOCAL');
  expect((await mockHub.runner(other.token, other.runner_id, 409)).status).toBe(409);
});

test('test_a_runner_only_config_error_halts_that_runner_while_the_host_serves', async ({ runnerHosts }) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  host.prepareStopped({ runnerSubscriptions: { 'runner-local': 'missing' } });
  await host.restart();
  expect((await host.api.health()).status).toBe(200);
  expect((await host.api.ready()).body.detail).toContain("names subscription 'missing'");
  expect((await host.runners['runner-local']!.expecting(503).status()).status).toBe(503);
  const cli = await host.cli(['status', '--dir', host.dir]);
  expect(cli.code).not.toBe(0);
  expect(cli.stdout + cli.stderr).toContain("names subscription 'missing'");
  expect(cli.stdout + cli.stderr).toContain('serves no runner');
});

test('test_two_held_runners_claim_and_lease_independently', async ({ mockHub, runnerHosts }) => {
  const projectB = await seedProject(mockHub, 'p-b');
  const host = await runnerHosts.create({ runnerConfig: { runner: [second] } });
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const [a, b] = await Promise.all([seedChunk(mockHub), seedChunk(mockHub, projectB)]);
  await host.stepHost();
  await passesUntil(host, 'runner-local', () => leased(host, 'runner-local', a));
  await passesUntil(host, 'runner-b', () => leased(host, 'runner-b', b));
  expect(await leased(host, 'runner-local', b)).toBe(false);
  expect(await leased(host, 'runner-b', a)).toBe(false);
  const done = async (id: string): Promise<boolean> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${id}`)).body.status === 'done';
  await passesUntil(host, 'runner-local', () => done(a));
  await passesUntil(host, 'runner-b', () => done(b));
});

test('test_a_refused_and_a_not_held_runner_halt_alone_while_the_held_runner_works', async ({ mockHub, runnerHosts }) => {
  await seedProject(mockHub, 'p-b');
  await seedProject(mockHub, 'p-c');
  const host = await runnerHosts.create({ runnerStarted: false, runnerConfig: { runner: [second, third] } });
  const refused = host.runners['runner-b']!;
  await mockHub.seed.revokeRunnerToken(refused.id);
  // The third runner's token resolves to a standalone identity, not a runner held by this host.
  const outside = (await mockHub.seed.runner({ name: 'outside', projects: ['p-c'] })).body;
  host.prepareStopped({ runnerTokens: { 'runner-c': outside.token }, runnerIds: { 'runner-c': outside.runner_id } });
  const standalone = host.runners['runner-c']!;
  await host.restart();
  await host.stepHost();
  await host.step('runner-b');
  await host.step('runner-c');
  expect((await refused.status()).body.identity?.state).toBe('refused');
  expect((await standalone.status()).body.identity?.state).toBe('not_held');
  for (const runner of [refused, standalone]) {
    const row = (await host.api.runners()).body.items.find((item) => item.runner_id === runner.id);
    expect(row).toMatchObject({ state: 'halted', served: true });
    expect(row?.halted_reasons).toHaveLength(1);
  }
  expect((await host.api.health()).status).toBe(200);
  const first = await seedChunk(mockHub);
  await passesUntil(host, 'runner-local', () => leased(host, 'runner-local', first));
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  await passesUntil(host, 'runner-local', async () => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${first}`)).body.status === 'done');
  const secondChunk = await seedChunk(mockHub);
  await passesUntil(host, 'runner-local', () => leased(host, 'runner-local', secondChunk));
  expect((await refused.leases()).body.items).toEqual([]);
  expect((await standalone.leases()).body.items).toEqual([]);
});

test('test_a_version_refused_runner_halts_alone_while_its_sibling_keeps_leasing', async ({ mockHub, runnerHosts }) => {
  await seedProject(mockHub, 'p-b');
  const host = await runnerHosts.create({ runnerStarted: false, runnerConfig: { runner: [second] } });
  const refused = host.runners['runner-b']!;
  const detail = "runner 0.1.4 is below hub 0.3.0's floor 0.2; upgrade it";
  await mockHub.levers.arm('version_refused', { payload: { runner_id: refused.id, reason: 'too-old', detail } });
  await host.restart();
  await host.stepHost();
  await host.step('runner-b');
  expect((await refused.status()).body.identity?.state).toBe('version_refused');
  expect((await host.api.runners()).body.items.find((item) => item.runner_id === refused.id)).toMatchObject({ state: 'halted', halted_reasons: [detail] });
  const first = await seedChunk(mockHub);
  await passesUntil(host, 'runner-local', () => leased(host, 'runner-local', first));
  expect((await refused.leases()).body.items).toEqual([]);
});
