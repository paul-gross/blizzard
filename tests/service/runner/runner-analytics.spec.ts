import { expect, holdingChunk, seededLeaseWithWorker, runnerTest as test } from '../../harness/fixtures';

// A worker's analytics reads through a real runner host, proxied to the test's own mock hub: the mock's seeded rows come
// back as the hub served them, and a chunk that is no routine run is refused rather than answered as an empty bucket.
// Each lease is a real one, held open by its judgement turn, and the worker calls carry its recorded spawn credential.

const RUNNER = 'runner-local';

test('test_a_workers_analytics_reads_proxy_through_to_the_mock_hubs_seeded_rows', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, {
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
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, holdingChunk());

  const refused = await runner.expecting(404).leaseAnalytics(leaseId, 'counts', 'files', { headers });
  expect(refused.status).toBe(404);
  expect((refused.body as unknown as { detail: string }).detail).toContain('no run context');
});
