import { expect, runnerTest as test, stepUntil, type MockChunkSpec } from '../../fixtures';

const RUNNER = 'runner-local';

/** A one-node chunk whose worker runs `prompt` as its spawn turn and then passes. */
function checkpointed(prompt: string): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt,
        judgement_prompt: "verdict('pass', 'the mock harness finished; checks are green')\n",
        choices: [{ name: 'pass', description: 'done', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

test('a worker held at a checkpoint keeps its lease open until released, then completes', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const environments = async () => (await runnerHost.api.environments()).body.workspaces.flatMap((workspace) => workspace.items);
  expect((await environments()).filter((environment) => environment.held)).toEqual([]);
  const chunkId = (await mockHub.seed.chunk(checkpointed(workerCheckpoint.snippet('gate')))).body.chunk_id;
  const lease = async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId);

  // Stepping reaches the checkpoint: the worker is there, and its lease is still open.
  let arrived = false;
  void workerCheckpoint.reached('gate').then(() => (arrived = true));
  await stepUntil(runnerHost, async () => arrived, (here) => here);
  await workerCheckpoint.reached('gate');
  expect((await lease())?.closed_at ?? null).toBeNull();
  const held = (await environments()).filter((environment) => environment.chunk_id === chunkId);
  expect(held).toHaveLength(1);
  expect(held[0]).toMatchObject({ held: true, chunk_id: chunkId, runner_id: runner.id });

  // Further passes leave it open: the worker is held, not slow.
  for (let pass = 0; pass < 3; pass++) expect((await runnerHost.step()).outcome).toBe('completed');
  expect((await lease())?.closed_at ?? null).toBeNull();

  workerCheckpoint.release('gate');
  const closed = await stepUntil(runnerHost, lease, (found) => found?.closed_at != null);
  expect(closed?.state).toBe('closed');
  const released = await environments();
  expect(released.filter((environment) => environment.held || environment.chunk_id === chunkId)).toEqual([]);
});

test('a checkpoint released before the worker arrives lets it straight through', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  workerCheckpoint.release('early');
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = (await mockHub.seed.chunk(checkpointed(workerCheckpoint.snippet('early')))).body.chunk_id;
  await stepUntil(runnerHost, async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId), (found) => found?.closed_at != null);
  await workerCheckpoint.reached('early');
});
