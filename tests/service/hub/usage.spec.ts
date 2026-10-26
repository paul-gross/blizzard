import { expect, test } from '../../harness/fixtures';

// Usage facts over the wire into a live hub: a mock runner claims a chunk, and usage pushed through the real
// `POST /api/fleet/events` derives per-node-step usage and the chunk total off `GET /api/chunks/{id}` and
// `GET /api/chunks`, an estimate kept apart from billed cost, idempotent on a replayed seq. Each test starts a mock
// runner and mints a runner token, which `mockRunner` and `fleet` admit only on a tenant of its own.
// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.
test.use({ isolation: 'fresh-tenant' });

/** `default-delivery`, build then deliver; the tests never leave `build`. */
const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'deliver' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    deliver: {
      executor: 'hub',
      action: 'pass',
      judgement: {
        choices: { success: { description: 'Delivered.', to: 'done' }, failure: { description: 'Failed to deliver.', to: 'build' } },
      },
    },
  },
});

interface UsagePayload {
  chunk_id: string;
  node_id: string;
  epoch: number;
  kind: 'spawn';
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_create_tokens: number;
  cost_usd: number | null;
  harness_id?: string;
  harness_version?: string;
  estimated_cost_usd?: number;
}

/** One `usage.recorded` payload; an N-1 runner omits the provenance keys, so `provenance: false` leaves them out. */
function usagePayload(
  chunkId: string,
  nodeId: string,
  epoch: number,
  amounts: { cost_usd: number | null; estimated_cost_usd?: number },
  provenance = true,
): UsagePayload {
  return {
    chunk_id: chunkId,
    node_id: nodeId,
    epoch,
    kind: 'spawn',
    model: 'claude-opus-4-8',
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 10,
    cache_create_tokens: 5,
    cost_usd: amounts.cost_usd,
    ...(provenance ? { harness_id: 'claude_code', harness_version: '1.2.3' } : {}),
    ...(amounts.estimated_cost_usd === undefined ? {} : { estimated_cost_usd: amounts.estimated_cost_usd }),
  };
}

test('test_hub_derives_chunk_usage_totals_off_a_live_api_from_pushed_facts', async ({ api, fleet, issue, mockRunner }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('usage totals over the wire');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const mock = await mockRunner('runner-usage');
  expect((await mock.drive('register')).body.status).toBe(201);
  const claim = (await mock.drive('claim', { chunk_id: chunkId })).body;
  expect(claim.claimed, JSON.stringify(claim)).toBe(true);
  const nodeId = claim.from_node_id as string;

  // The claim minted a real lease and epoch on the running hub.
  const epoch = (await api.get(`/api/chunks/${chunkId}`)).body.latest_epoch ?? 1;

  const driver = await fleet('fleet-driver');
  const push = async (seq: number, payload: UsagePayload) => {
    const pushed = await driver.post('/api/fleet/events', { data: { facts: [{ seq, kind: 'usage.recorded', payload }] } });
    expect(pushed.status, JSON.stringify(pushed.body)).toBe(200);
    return pushed.body as { applied: number[]; already_applied: number[] };
  };

  // Three facts: one carrying a cost, one with cost absent, and one from a runner predating harness provenance.
  expect((await push(1, usagePayload(chunkId, nodeId, epoch, { cost_usd: 0.1 }))).applied).toEqual([1]);
  expect((await push(2, usagePayload(chunkId, nodeId, epoch, { cost_usd: null }))).applied).toEqual([2]);
  expect((await push(3, usagePayload(chunkId, nodeId, epoch, { cost_usd: 0.05 }, false))).applied).toEqual([3]);

  let detail = (await api.get(`/api/chunks/${chunkId}`)).body;
  expect(detail.usage).toHaveLength(3);
  const step = detail.usage[0];
  expect(step.node_id).toBe(nodeId);
  expect(step.epoch).toBe(epoch);
  expect(step.input_tokens).toBe(100);
  expect(step.cache_create_tokens).toBe(5);
  // The pushed harness stamp reads back verbatim.
  expect(step.harness_id).toBe('claude_code');
  expect(step.harness_version).toBe('1.2.3');
  // The N-1 runner's fact reads back null, never rejected and never defaulted to the sibling rows' stamp.
  expect(detail.usage[2].harness_id).toBeNull();
  expect(detail.usage[2].harness_version).toBeNull();

  const total = detail.cost;
  expect(total.input_tokens).toBe(300); // all three rows summed by class
  expect(total.output_tokens).toBe(150);
  expect(total.cost_usd).toBeCloseTo(0.15); // the two cost-bearing rows, the lower bound
  expect(total.cost_partial).toBe(true); // a cost-absent row flags the total partial

  const listing = (await api.get('/api/chunks')).body.chunks as { chunk_id: string; cost: typeof total }[];
  const row = listing.find((candidate) => candidate.chunk_id === chunkId);
  expect(row?.cost.cost_usd).toBeCloseTo(0.15);
  expect(row?.cost.cost_partial).toBe(true);

  // A re-pushed seq lands nothing twice.
  const replay = await push(2, usagePayload(chunkId, nodeId, epoch, { cost_usd: null }));
  expect(replay.applied).toEqual([]);
  expect(replay.already_applied).toEqual([2]);
  detail = (await api.get(`/api/chunks/${chunkId}`)).body;
  expect(detail.usage, 'the replayed usage fact was applied twice').toHaveLength(3);
  expect(detail.cost.input_tokens).toBe(300);
});

test('test_hub_reads_back_an_estimate_apart_from_billed_cost_off_a_live_api', async ({ api, fleet, issue, mockRunner }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const { chunk } = await issue('usage estimate over the wire');
  const chunkId = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const mock = await mockRunner('runner-usage-estimate');
  expect((await mock.drive('register')).body.status).toBe(201);
  const claim = (await mock.drive('claim', { chunk_id: chunkId })).body;
  expect(claim.claimed, JSON.stringify(claim)).toBe(true);
  const nodeId = claim.from_node_id as string;
  const epoch = (await api.get(`/api/chunks/${chunkId}`)).body.latest_epoch ?? 1;

  const driver = await fleet('fleet-driver');
  const pushed = await driver.post('/api/fleet/events', {
    data: { facts: [{ seq: 1, kind: 'usage.recorded', payload: usagePayload(chunkId, nodeId, epoch, { cost_usd: null, estimated_cost_usd: 0.03 }) }] },
  });
  expect(pushed.status, JSON.stringify(pushed.body)).toBe(200);
  expect(pushed.body.applied).toEqual([1]);

  const detail = (await api.get(`/api/chunks/${chunkId}`)).body;
  const step = detail.usage[0];
  expect(step.cost_usd).toBeNull();
  expect(step.estimated_cost_usd).toBeCloseTo(0.03);

  const total = detail.cost;
  expect(total.cost_usd).toBe(0);
  expect(total.estimated_cost_usd).toBeCloseTo(0.03);
  expect(total.cost_partial).toBe(false); // an estimate is not a lower bound

  const listing = (await api.get('/api/chunks')).body.chunks as { chunk_id: string; cost: typeof total }[];
  const row = listing.find((candidate) => candidate.chunk_id === chunkId);
  expect(row?.cost.estimated_cost_usd).toBeCloseTo(0.03);
  expect(row?.cost.cost_partial).toBe(false);
});
