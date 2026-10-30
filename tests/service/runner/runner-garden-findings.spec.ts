import { expect, holdingChunk, seededLeaseWithWorker, runnerTest as test } from '../../harness/fixtures';

// The garden-findings proxy leg: the mock hub's `GET /api/fleet/chunks/{id}/garden/findings` is what a runner's
// `GET /api/leases/{id}/garden/findings` proxies to, called with only the lease's recorded worker credential. Each
// lease is held open at a checkpoint in its spawn turn.

const RUNNER = 'runner-local';

test('test_a_workers_garden_findings_read_proxies_through_to_the_mock_hubs_bucket', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, workerCheckpoint, {
    ...holdingChunk(workerCheckpoint),
    garden_run: { routine_name: 'nightly', scope_slug: 'blizzard' },
    garden_findings: [
      { finding_id: 'fin_1', class: 'stale-docstring', locus: 'a.py:1', summary: 's' },
      { finding_id: 'fin_2', class: 'c', locus: 'a.py:2', summary: 's', live: false, state: 'resolved' },
    ],
  });

  const findings = await runnerHost.runners[RUNNER]!.leaseGardenFindings(leaseId, { headers });
  expect(findings.status).toBe(200);
  expect(findings.body.map((row) => row.finding_id)).toEqual(['fin_1']);
  expect(findings.body[0]).toMatchObject({ routine_name: 'nightly', scope_slug: 'blizzard' });
});

test('test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_list', async ({ mockHub, runnerHost, workerCheckpoint }) => {
  // No garden_run at all: not a routine run.
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, workerCheckpoint);

  const findings = await runnerHost.runners[RUNNER]!.leaseGardenFindings(leaseId, { headers });
  expect(findings.status).toBe(404);
  expect((findings.body as unknown as { detail: string }).detail).toContain('no run context');
});
