import { expect, stepUntil, type MockChunkSpec, runnerTest as test } from '../../harness/fixtures';

// A real runner host stepped against the test's own mock hub: the usage facts a worker earns ride the runner's
// store-and-forward buffer through a hub outage and reach the hub once. Every wait is a step of the host; every read
// is a public surface.

const RUNNER = 'runner-local';

const BUILD_THEN_DONE: MockChunkSpec = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      prompt: 'pass\n',
      judgement_prompt: "verdict('pass', 'the change is committed and green')\n",
      choices: [{ name: 'pass', description: 'committed and green', to: 'done' }],
      retries_max: 1,
    },
  },
  work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
};

interface DeliveredUsage {
  seq: number;
  kind: string;
  payload: { chunk_id: string; kind: string; harness_id: string; input_tokens: number; output_tokens: number; cost_usd: number | null };
}

test('test_runner_buffers_usage_facts_through_a_hub_outage_and_flushes_once', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const seeded = await mockHub.seed.chunk(BUILD_THEN_DONE);
  expect(seeded.status).toBe(201);
  const chunkId: string = seeded.body.chunk_id;

  const usageFacts = async () => (await runner.facts()).body.items.filter((fact) => fact.kind === 'usage.recorded' && fact.chunk_id === chunkId);
  const events = async (): Promise<DeliveredUsage[]> =>
    (await mockHub.captured.read()).body.requests
      .filter((request) => request.method === 'POST' && request.path === '/api/fleet/events')
      .flatMap((request) => (request.body as { facts: DeliveredUsage[] }).facts)
      .filter((fact) => fact.kind === 'usage.recorded' && fact.payload.chunk_id === chunkId);
  const status = async (): Promise<string> => ((await mockHub.fleetOf(runnerHost, RUNNER).get(`/api/fleet/chunks/${chunkId}`)).body as { status: string }).status;

  // The worker's usage is recorded by the runner before the hub has taken it: this chunk's facts, pending, chunk still running.
  const pending = await stepUntil(runnerHost, usageFacts, (facts) => facts.length > 0);
  const seqs = pending.map((fact) => fact.seq);
  expect(pending.every((fact) => fact.acked_at === null)).toBe(true);
  expect(await status()).not.toBe('done');
  const lease = (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId)!;
  const earned = (await runner.leaseUsage(lease.lease_id)).body.items;
  expect(earned.length).toBe(seqs.length);
  expect(earned.every((fact) => fact.harness_id === 'claude_code')).toBe(true);

  // The hub goes unreachable: the runner attempts the push, and each usage fact stays unacknowledged under its seq.
  await mockHub.levers.arm('unreachable', { remaining: 10_000 });
  const attempts = async (): Promise<number> => (await mockHub.captured.read()).body.requests.filter((request) => request.path === '/api/fleet/events').length;
  const before = await attempts();
  await stepUntil(runnerHost, attempts, (count) => count > before);
  const during = await usageFacts();
  expect(during.map((fact) => fact.seq)).toEqual(seqs);
  expect(during.every((fact) => fact.acked_at === null)).toBe(true);
  expect(await runner.bufferDepth()).toBeGreaterThanOrEqual(seqs.length);

  // The hub heals: the buffer drains, the chunk lands, and the facts are acknowledged under the same seqs. The mock
  // captures a refused push too, so the capture is cleared first and holds only what the healed hub was sent.
  await mockHub.captured.reset();
  await mockHub.levers.clear('unreachable');
  await stepUntil(runnerHost, status, (current) => current === 'done');
  const acked = await usageFacts();
  expect(acked.map((fact) => fact.seq)).toEqual(seqs);
  expect(acked.every((fact) => fact.acked_at !== null)).toBe(true);
  expect(await runner.bufferDepth()).toBe(0);

  // The hub holds exactly the worker's usage: one delivery per seq, summing to what the lease recorded.
  const delivered = await events();
  expect(delivered.map((fact) => fact.seq).sort()).toEqual([...seqs].sort());
  expect(delivered.every((fact) => fact.payload.harness_id === 'claude_code')).toBe(true);
  const total = (rows: { input_tokens: number; output_tokens: number; cost_usd: number | null }[]) => ({
    input: rows.reduce((sum, row) => sum + row.input_tokens, 0),
    output: rows.reduce((sum, row) => sum + row.output_tokens, 0),
    cost: rows.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0),
  });
  const want = total(earned);
  const got = total(delivered.map((fact) => fact.payload));
  expect(got.input).toBe(want.input);
  expect(got.output).toBe(want.output);
  expect(got.cost).toBeCloseTo(want.cost, 9);

  // Recorded once: further passes of the host record no new usage fact and deliver none again.
  await runnerHost.step();
  await runnerHost.step();
  expect((await usageFacts()).map((fact) => fact.seq)).toEqual(seqs);
  expect((await events()).length).toBe(seqs.length);
});
