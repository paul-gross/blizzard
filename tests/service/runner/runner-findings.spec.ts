import { expect, type MockChunkSpec, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A worker holding only its lease reads its chunk's answered findings through the runner, which proxies to the mock hub.
// The read is made with the credential the mock harness recorded for the genuine lease it spawned.

const RUNNER = 'runner-local';
const MAX_STEPS = 40;

/** Holds the worker's judgement turn, and so its lease, open: the spawn turn has ended and recorded its session. */
const HOLD_OPEN = 'import time\ntime.sleep(120)\n';

const ANSWERED = [
  { finding_id: 'fin_1', routine_name: 'nightly', scope_slug: 'blizzard', class: 'stale-docstring', locus: 'a.py:1', summary: 's' },
];

function chunkSpec(answered?: MockChunkSpec['garden_answered_findings']): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: 'print("built")\n',
        judgement_prompt: HOLD_OPEN,
        choices: [{ name: 'pass', description: 'built', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
    ...(answered ? { garden_answered_findings: answered } : {}),
  };
}

/** Steps the host until its chunk's lease runs and the credential the runner issued its worker answers. */
async function leaseWithWorker(host: RunnerHost, chunkId: string) {
  const runner = host.runners[RUNNER]!;
  for (let pass = 0; ; pass++) {
    if (pass === MAX_STEPS) throw new Error(`no readable running lease within ${MAX_STEPS} passes\n${host.logTail()}`);
    const lease = (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running');
    if (lease) {
      try {
        const headers = host.workerHeaders(lease.lease_id);
        if ((await runner.leaseArtifacts(lease.lease_id, { scope: 'graph', headers })).status === 200) return { lease, headers };
      } catch {
        // The runner re-mints the token for each resume; the record is read again after the next step.
      }
    }
    expect((await host.step()).outcome).toBe('completed');
  }
}

test('test_a_workers_finding_reads_proxy_through_to_the_mock_hubs_answered_set', async ({ mockHub, runnerHost }) => {
  const seeded = await mockHub.seed.chunk(chunkSpec(ANSWERED));
  expect(seeded.status).toBe(201);
  const { lease, headers } = await leaseWithWorker(runnerHost, seeded.body.chunk_id);
  const runner = runnerHost.runners[RUNNER]!;

  const listed = await runner.raw.get<typeof ANSWERED>(`/api/leases/${lease.lease_id}/findings`, { headers });
  expect(listed.status).toBe(200);
  expect(listed.body).toHaveLength(1);
  expect(listed.body[0]).toMatchObject(ANSWERED[0]!);

  const one = await runner.raw.get(`/api/leases/${lease.lease_id}/findings/fin_1`, { headers });
  expect(one.status).toBe(200);
  expect(one.body).toMatchObject(ANSWERED[0]!);
});

test('test_a_leases_chunk_answering_no_proposal_is_a_legible_refusal_not_an_empty_list', async ({ mockHub, runnerHost }) => {
  const seeded = await mockHub.seed.chunk(chunkSpec());
  expect(seeded.status).toBe(201);
  const { lease, headers } = await leaseWithWorker(runnerHost, seeded.body.chunk_id);
  const runner = runnerHost.runners[RUNNER]!;

  const refused = await runner.expecting(404).raw.get<{ detail: string }>(`/api/leases/${lease.lease_id}/findings`, { headers });
  expect(refused.status).toBe(404);
  expect(refused.body.detail).toContain('no accepted, minted garden proposal');
});
