import { expect, runnerTest as test, stepUntil } from '../../harness/fixtures';

test('a legacy identity refusal halts the runner until its credential is rotated', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  const runner = host.runners['runner-local']!;
  await mockHub.seed.revokeRunnerToken(runner.id);
  await mockHub.levers.arm('legacy_identity_refusals', { remaining: 1 });

  await host.restart();
  await host.step();
  expect((await runner.status()).body.identity?.state).toBe('refused');
  expect((await host.api.runners()).body.items.find((item) => item.runner_id === runner.id)).toMatchObject({ state: 'halted', served: true });
  expect((await mockHub.levers.list()).body.active.some((lever) => lever.kind === 'legacy_identity_refusals')).toBe(false);

  const chunkId = (await mockHub.seed.chunk({ entry: 'build', nodes: {
    build: { executor: 'runner', prompt: "print('worked')\n", judged_by: 'worker', judgement_prompt: "verdict('pass', 'done')\n", choices: [{ name: 'pass', to: 'done' }] },
  } })).body.chunk_id;
  await host.step();
  expect((await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId)).toEqual([]);

  const token = (await mockHub.enroll.rotate(runner.id)).body.token;
  await host.stop();
  host.prepareStopped({ runnerTokens: { 'runner-local': token } });
  await host.restart();
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  await stepUntil(host, async () => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.status as string, (status) => status === 'done');
  expect((await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId)).toMatchObject([{ state: 'closed' }]);
});
