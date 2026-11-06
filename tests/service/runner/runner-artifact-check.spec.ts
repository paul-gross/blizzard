import { expect, runningLeaseWithWorker, runnerTest as test, SPAWN_HOLD, type MockChunkSpec, type WorkerCheckpoint } from '../../harness/fixtures';

// A worker staging an artifact has the runner ask the hub whether the content meets the format its graph owes the name.

const RUNNER = 'runner-local';
const REFUSAL = "artifact 'delta' does not match the finding-delta shape: measurement: Input should be a valid string";
const VALID = '{"scope":"s"}';
const INVALID = '{"scope":"s","measurement":1}';

function chunkSpec(checkpoint: WorkerCheckpoint): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: checkpoint.snippet(SPAWN_HOLD),
        judgement_prompt: "verdict('pass', 'built')\n",
        choices: [{ name: 'pass', description: 'built', to: 'deliver' }],
        retries_max: 1,
      },
      deliver: {
        executor: 'hub',
        judgement: { choices: { landed: { description: 'Landed.', to: 'done' } } },
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
    artifact_formats: { delta: ['finding-delta'] },
  };
}

test('test_a_hub_refusal_blocks_staging_and_no_refusal_stages', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  const seeded = await mockHub.seed.chunk(chunkSpec(workerCheckpoint));
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(runnerHost, seeded.body.chunk_id, workerCheckpoint);
  const runner = runnerHost.runners[RUNNER]!;
  const path = `/api/leases/${lease.lease_id}/attachments`;

  const staged = await runner.raw.post(path, { data: { name: 'delta', content: VALID }, headers });
  expect(staged.status).toBe(200);

  const refused = await runner.expecting(422).raw.post<{ detail: string }>(path, { data: { name: 'delta', content: INVALID }, headers });
  expect(refused.status).toBe(422);
  expect(refused.body.detail).toBe(REFUSAL);

  const listed = await runner.raw.get<{ name: string; content: string }[]>(path, { headers });
  expect(listed.body).toEqual([{ name: 'delta', content: VALID }]);
});
