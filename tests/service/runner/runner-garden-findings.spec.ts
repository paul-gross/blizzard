import { expect, type MockChunkSpec, runnerTest as test } from '../../harness/fixtures';
import type { RunnerHost } from '../../harness/fixtures';

// The garden-findings proxy leg: the mock hub's `GET /api/fleet/chunks/{id}/garden/findings` is what a runner's
// `GET /api/leases/{id}/garden/findings` proxies to, called with only the lease's recorded worker credential.

const RUNNER = 'runner-local';
const MAX_STEPS = 40;

/** A one-node chunk whose worker holds its lease open on a long judgement, so the lease can be called as. */
const SPEC: MockChunkSpec = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      prompt: "print('worked')\n",
      judgement_prompt: 'import time\ntime.sleep(120)\n',
      choices: [{ name: 'pass', description: 'done', to: 'deliver' }],
    },
    deliver: { executor: 'hub', judgement: { choices: { landed: { description: 'Landed.', to: 'done' } } } },
  },
};

/** Steps the host until `read` answers something other than `undefined`, failing after `MAX_STEPS` passes. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T | undefined>): Promise<T> {
  for (let pass = 0; pass < MAX_STEPS; pass++) {
    const state = await read();
    if (state !== undefined) return state;
    await host.step();
  }
  throw new Error(`not reached within ${MAX_STEPS} passes\n${host.logTail()}`);
}

/** The running lease for `chunkId` and the worker headers its recorded credential makes. The token is re-minted per resume, so the record is read until the one it holds answers. */
async function workerLane(runnerHost: RunnerHost, chunkId: string): Promise<{ leaseId: string; headers: Record<string, string> }> {
  const runner = runnerHost.runners[RUNNER]!;
  const lease = await stepUntil(runnerHost, async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'));
  const headers = await stepUntil(runnerHost, async () => {
    try {
      const candidate = runnerHost.workerHeaders(lease.lease_id);
      return (await runner.leaseArtifacts(lease.lease_id, { scope: 'graph', headers: candidate })).status === 200 ? candidate : undefined;
    } catch {
      return undefined;
    }
  });
  return { leaseId: lease.lease_id, headers };
}

test('test_a_workers_garden_findings_read_proxies_through_to_the_mock_hubs_bucket', async ({ mockHub, runnerHost }) => {
  const seeded = await mockHub.seed.chunk({
    ...SPEC,
    garden_run: { routine_name: 'nightly', scope_slug: 'blizzard' },
    garden_findings: [
      { finding_id: 'fin_1', class: 'stale-docstring', locus: 'a.py:1', summary: 's' },
      { finding_id: 'fin_2', class: 'c', locus: 'a.py:2', summary: 's', live: false, state: 'resolved' },
    ],
  });
  expect(seeded.status).toBe(201);
  const { leaseId, headers } = await workerLane(runnerHost, seeded.body.chunk_id);

  const findings = await runnerHost.runners[RUNNER]!.leaseGardenFindings(leaseId, { headers });
  expect(findings.status).toBe(200);
  expect(findings.body.map((row) => row.finding_id)).toEqual(['fin_1']);
  expect(findings.body[0]).toMatchObject({ routine_name: 'nightly', scope_slug: 'blizzard' });
});

test('test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_list', async ({ mockHub, runnerHost }) => {
  // No garden_run at all: not a routine run.
  const seeded = await mockHub.seed.chunk(SPEC);
  expect(seeded.status).toBe(201);
  const { leaseId, headers } = await workerLane(runnerHost, seeded.body.chunk_id);

  const findings = await runnerHost.runners[RUNNER]!.leaseGardenFindings(leaseId, { headers });
  expect(findings.status).toBe(404);
  expect((findings.body as unknown as { detail: string }).detail).toContain('no run context');
});
