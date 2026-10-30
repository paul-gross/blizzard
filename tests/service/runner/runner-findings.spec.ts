import { expect, type MockChunkSpec, runningLeaseWithWorker, runnerTest as test, SPAWN_HOLD, type WorkerCheckpoint } from '../../harness/fixtures';

// A worker holding only its lease reads its chunk's answered findings through the runner, which proxies to the mock hub.
// The read is made with the credential the mock harness recorded for the genuine lease it spawned, held open at a
// checkpoint in its spawn turn.

const RUNNER = 'runner-local';

const ANSWERED = [
  { finding_id: 'fin_1', routine_name: 'nightly', scope_slug: 'blizzard', class: 'stale-docstring', locus: 'a.py:1', summary: 's' },
];

function chunkSpec(checkpoint: WorkerCheckpoint, answered?: MockChunkSpec['garden_answered_findings']): MockChunkSpec {
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
    ...(answered ? { garden_answered_findings: answered } : {}),
  };
}

test('test_a_workers_finding_reads_proxy_through_to_the_mock_hubs_answered_set', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  const seeded = await mockHub.seed.chunk(chunkSpec(workerCheckpoint, ANSWERED));
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(runnerHost, seeded.body.chunk_id, workerCheckpoint);
  const runner = runnerHost.runners[RUNNER]!;

  const listed = await runner.raw.get<typeof ANSWERED>(`/api/leases/${lease.lease_id}/findings`, { headers });
  expect(listed.status).toBe(200);
  expect(listed.body).toHaveLength(1);
  expect(listed.body[0]).toMatchObject(ANSWERED[0]!);

  const one = await runner.raw.get(`/api/leases/${lease.lease_id}/findings/fin_1`, { headers });
  expect(one.status).toBe(200);
  expect(one.body).toMatchObject(ANSWERED[0]!);
});

test('test_a_leases_chunk_answering_no_proposal_is_a_legible_refusal_not_an_empty_list', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  const seeded = await mockHub.seed.chunk(chunkSpec(workerCheckpoint));
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(runnerHost, seeded.body.chunk_id, workerCheckpoint);
  const runner = runnerHost.runners[RUNNER]!;

  const refused = await runner.expecting(404).raw.get<{ detail: string }>(`/api/leases/${lease.lease_id}/findings`, { headers });
  expect(refused.status).toBe(404);
  expect(refused.body.detail).toContain('no accepted, minted garden proposal');
});
