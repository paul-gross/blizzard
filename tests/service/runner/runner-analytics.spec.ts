import { expect, type MockChunkSpec, type MockHub, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A worker's analytics reads through a real runner host, proxied to the test's own mock hub: the mock's seeded rows come
// back as the hub served them, and a chunk that is no routine run is refused rather than answered as an empty bucket.
// Each lease is a real one, held open by its judgement turn, and the worker calls carry its recorded spawn credential.

const RUNNER = 'runner-local';
const MAX_STEPS = 40;
/** Holds the worker's judgement turn, and so its lease, open: the spawn turn has ended and recorded its session. */
const HOLD_OPEN = 'import time\ntime.sleep(120)\n';

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

/** Steps the host's sole runner until `reached` holds, failing after `MAX_STEPS` passes with the last state it saw. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
  }
  return state;
}

/** Seeds `spec` and steps until its sole lease is running and answers to the spawn credential the mock harness recorded for it. */
async function runningLeaseWithWorker(mockHub: MockHub, host: RunnerHost, spec: MockChunkSpec) {
  const seeded = await mockHub.seed.chunk(spec);
  expect(seeded.status).toBe(201);
  const chunkId = seeded.body.chunk_id;
  const runner = host.runners[RUNNER]!;
  const lease = await stepUntil(
    host,
    async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'),
    (found) => found !== undefined,
  );
  const headers = await stepUntil(
    host,
    async () => {
      try {
        // The runner re-mints a lease's token for each resume, so the record is read until the credential it holds answers.
        const candidate = host.workerHeaders(lease!.lease_id);
        return (await runner.leaseArtifacts(lease!.lease_id, { scope: 'graph', headers: candidate })).status === 200 ? candidate : undefined;
      } catch {
        return undefined;
      }
    },
    (found) => found !== undefined,
  );
  return { leaseId: lease!.lease_id, headers: headers! };
}

test('test_a_workers_analytics_reads_proxy_through_to_the_mock_hubs_seeded_rows', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const { leaseId, headers } = await runningLeaseWithWorker(mockHub, runnerHost, {
    ...holdingChunk(),
    garden_run: { routine_name: 'nightly', scope_slug: 'blizzard' },
    analytics: {
      counts_skills: [{ key: 'wf-commit', count: 2 }],
      spend_nodes: [
        {
          key: 'nd_build',
          graph_name: 'default-delivery',
          node_name: 'build',
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 10,
          cache_create_tokens: 5,
          cost_usd: 0.1,
          cost_partial: false,
        },
      ],
    },
  });

  const counts = await runner.leaseAnalytics(leaseId, 'counts', 'skills', { headers });
  expect(counts.status).toBe(200);
  expect(counts.body.counts).toEqual([{ key: 'wf-commit', count: 2, graph_name: null, node_name: null }]);

  const spend = await runner.leaseAnalytics(leaseId, 'spend', 'nodes', { headers });
  expect(spend.status).toBe(200);
  const rows = spend.body.spend as Record<string, unknown>[];
  expect(rows[0]).toMatchObject({ key: 'nd_build', graph_name: 'default-delivery', node_name: 'build' });

  // The mock serves its seeded rows as-is, so the by-name read answers the same rows.
  const byName = await runner.leaseAnalytics(leaseId, 'spend', 'nodes', { headers, params: { by_name: 'true' } });
  expect(byName.status).toBe(200);
  expect(byName.body.spend).toEqual(rows);
});

test('test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_bucket', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  // No `garden_run` at all: the chunk is no routine run.
  const { leaseId, headers } = await runningLeaseWithWorker(mockHub, runnerHost, holdingChunk());

  const refused = await runner.expecting(404).leaseAnalytics(leaseId, 'counts', 'files', { headers });
  expect(refused.status).toBe(404);
  expect((refused.body as unknown as { detail: string }).detail).toContain('no run context');
});
