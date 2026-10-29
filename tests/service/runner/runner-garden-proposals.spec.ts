import { expect, HOLD_OPEN, runningLeaseWithWorker, type MockChunkSpec, type MockHub, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A worker's garden-proposals read through a real runner host, proxied to the test's own mock hub: the routine's own
// open docket, the `state` selector reaching the closed bucket, and a chunk that is no routine run refused rather than
// answered as an empty list. Each lease is a real one, held open by its judgement turn, and the worker calls carry its
// recorded spawn credential.

const RUNNER = 'runner-local';

function holdingChunk(): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: 'pass\n',
        judgement_prompt: HOLD_OPEN,
        choices: [{ name: 'pass', description: 'committed and green', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

const OPEN_PROPOSAL = { proposal_id: 'prop_1', class: 'mechanize', title: 't', body: 'b', findings: ['fin_1'] };

/** Seeds `spec` and steps until its sole lease is running with a worker credential that answers. */
async function seededLeaseWithWorker(mockHub: MockHub, host: RunnerHost, spec: MockChunkSpec) {
  const seeded = await mockHub.seed.chunk(spec);
  expect(seeded.status).toBe(201);
  const { lease, headers } = await runningLeaseWithWorker(host, seeded.body.chunk_id);
  return { leaseId: lease.lease_id, headers };
}

test('test_a_workers_garden_proposals_read_proxies_through_to_the_mock_hubs_bucket', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, {
    ...holdingChunk(),
    garden_run: { routine_name: 'nightly', scope_slug: 'blizzard' },
    garden_proposals: [OPEN_PROPOSAL],
  });

  const proposals = await runner.leaseGardenProposals(leaseId, { headers });
  expect(proposals.status).toBe(200);
  expect(proposals.body.map((r) => r.proposal_id)).toEqual(['prop_1']);
  expect(proposals.body[0]).toMatchObject({ routine_name: 'nightly', findings: ['fin_1'] });
});

test('test_a_workers_state_flag_reaches_the_mock_hubs_closed_bucket', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, {
    ...holdingChunk(),
    garden_run: { routine_name: 'nightly', scope_slug: 'blizzard' },
    garden_proposals: [
      OPEN_PROPOSAL,
      { ...OPEN_PROPOSAL, proposal_id: 'prop_closed', closure: { closure: 'passed', reason: 'not worth it' } },
    ],
  });

  const proposals = await runner.leaseGardenProposals(leaseId, { headers, params: { state: 'closed' } });
  expect(proposals.status).toBe(200);
  expect(proposals.body.map((r) => r.proposal_id)).toEqual(['prop_closed']);
  expect(proposals.body[0]!.closure?.reason).toBe('not worth it');
});

test('test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_list', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  // No `garden_run` at all: the chunk is no routine run.
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, holdingChunk());

  const refused = await runner.expecting(404).leaseGardenProposals(leaseId, { headers });
  expect(refused.status).toBe(404);
  expect((refused.body as unknown as { detail: string }).detail).toContain('no run context');
});
