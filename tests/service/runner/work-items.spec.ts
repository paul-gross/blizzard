import { expect, holdingChunk, seededLeaseWithWorker, runnerTest as test } from '../../harness/fixtures';

// A worker's work-items read through a real runner host, proxied to the test's own mock hub. Each case runs a real lease,
// held open by its judgement turn, and reads as the worker would: by the spawn credential the mock harness recorded.

const RUNNER = 'runner-local';
const WORK_REF = 'toy-api/issues/1';

test('test_work_items_proxy_carries_the_widened_fields_unchanged_over_a_real_hop', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const spec = { ...holdingChunk(), chunk_id: 'ch_work_items_hop' };
  const { leaseId } = await seededLeaseWithWorker(mockHub, runnerHost, spec);

  const proxied = await runner.workItems(spec.chunk_id, { headers: runnerHost.workerHeaders(leaseId, 'lease-token') });
  expect(proxied.status).toBe(200);
  expect(proxied.body.items).toHaveLength(1);
  const entry = proxied.body.items[0]!;
  expect(entry.source).toBe('mock');
  expect(entry.ref).toBe(WORK_REF);
  // The mock's canned read has no author or priority lever: the widened keys survive the hop, they are not filled in.
  expect(entry).toHaveProperty('author', null);
  expect(entry).toHaveProperty('stated_priority', null);

  // Every key the hub served arrives as served; `fetched_at` is stamped on each read, so it alone differs.
  const served = await mockHub.fleetOf(runnerHost, RUNNER).get(`/api/fleet/chunks/${spec.chunk_id}/work-items`);
  expect(served.status).toBe(200);
  const withoutStamp = (items: Record<string, unknown>[]): Record<string, unknown>[] => items.map(({ fetched_at: _stamped, ...rest }) => rest);
  expect(withoutStamp(proxied.body.items as unknown as Record<string, unknown>[])).toEqual(withoutStamp(served.body.items));
});

test.describe('a hub restart under an in-flight read', () => {
  test.use({ mockHubRelay: true });

  test('test_a_worker_lane_proxied_read_rides_out_a_real_mock_hub_restart', async ({ mockHub, runnerHost }) => {
    const relay = mockHub.relay!;
    const runner = runnerHost.runners[RUNNER]!;
    const spec = { ...holdingChunk(), chunk_id: 'ch_work_items_restart' };
    const { leaseId } = await seededLeaseWithWorker(mockHub, runnerHost, spec);
    const headers = runnerHost.workerHeaders(leaseId, 'lease-token');

    // The outage stays open under the read until the restarted hub knows the runner and the chunk: a retry that landed on
    // a hub that did not would be refused for good, not retried.
    relay.hold();
    await mockHub.stop();
    const read = runner.workItems(spec.chunk_id, { headers });
    await relay.refusal();

    await mockHub.start();
    const reseededRunner = await mockHub.seed.runner({
      name: RUNNER,
      runner_id: runner.id,
      token: runnerHost.hubTokens().runners[RUNNER]!,
    });
    expect(reseededRunner.status).toBe(201);
    expect((await mockHub.seed.chunk(spec)).status).toBe(201);
    await mockHub.captured.reset();
    relay.release();

    const answered = await read;
    expect(answered.status).toBe(200);
    expect(answered.body.items.map((item) => item.ref)).toEqual([WORK_REF]);

    // The read landed on the restarted process, under the runner's own credential.
    const forwarded = (await mockHub.captured.read()).body.requests.filter((request) => request.path === `/api/fleet/chunks/${spec.chunk_id}/work-items`);
    expect(forwarded.length).toBeGreaterThan(0);
    expect(forwarded.at(-1)!.headers.authorization).toBe(`Bearer ${runnerHost.hubTokens().runners[RUNNER]}`);
  });
});
