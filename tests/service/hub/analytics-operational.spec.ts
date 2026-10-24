import { expect, test } from '../../harness/fixtures';

// The analytics operational-datasets routes over a real hub: durations, spend (by node and by model), and outcomes each
// reflect a real completed step over the packaged default graph's own entry node. The routes read tenant-wide
// aggregates, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

test('test_the_three_datasets_reflect_a_real_completed_step_over_a_live_hub', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('analytics operational datasets over a real hub');
  const chunkId = chunk.chunk_id;
  const detail = await api.get(`/api/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  const nodeId: string = detail.body.current_node_id;
  expect(nodeId).not.toBeNull();

  // Ingest rests not-ready, and the hub grants a claim only to a registered runner.
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const runner = await fleet('r1');
  const registered = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'w1', capabilities: [{ harness_id: 'claude', default: true }] },
  });
  expect(registered.status).toBe(201);
  // The claim reserves epoch 1 for r1 — the epoch its mint and completion land at.
  const claim = await runner.post('/api/fleet/routes', { data: { chunk_id: chunkId, workspace_id: 'w1', environment_ids: [] } });
  expect(claim.status).toBe(201);

  const lease = await runner.post('/api/fleet/events', {
    data: { facts: [{ seq: 1, kind: 'lease.minted', payload: { chunk_id: chunkId, epoch: 1 } }] },
  });
  expect(lease.status).toBe(200);
  const usage = {
    chunk_id: chunkId,
    node_id: nodeId,
    epoch: 1,
    kind: 'spawn',
    model: 'claude-opus-4-8',
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 10,
    cache_create_tokens: 5,
    cost_usd: 0.1,
  };
  const recorded = await runner.post('/api/fleet/events', { data: { facts: [{ seq: 2, kind: 'usage.recorded', payload: usage }] } });
  expect(recorded.status).toBe(200);
  // `already-done` closes the packaged default graph's `triage` entry node without entering a lane — no worktree needed
  // for this shape assertion.
  const completion = await runner.post(`/api/fleet/chunks/${chunkId}/completions`, {
    data: { choice: 'already-done', epoch: 1, from_node_id: nodeId },
  });
  expect(completion.status).toBe(200);

  const durations = await api.get('/api/analytics/durations/nodes');
  expect(durations.status).toBe(200);
  const byNode = Object.fromEntries(durations.body.durations.map((row: { key: string }) => [row.key, row]));
  expect(byNode[nodeId].completed_steps).toBe(1);

  const spend = await api.get('/api/analytics/spend/nodes');
  expect(spend.status).toBe(200);
  const spendByNode = Object.fromEntries(spend.body.spend.map((row: { key: string }) => [row.key, row]));
  expect(spendByNode[nodeId].input_tokens).toBe(100);
  expect(spendByNode[nodeId].cost_partial).toBe(false);

  const byModel = await api.get('/api/analytics/spend/models');
  expect(byModel.status).toBe(200);
  expect(Object.fromEntries(byModel.body.spend.map((row: { key: string; input_tokens: number }) => [row.key, row.input_tokens]))).toEqual({
    'claude-opus-4-8': 100,
  });
  const otherModel = await api.get('/api/analytics/durations/nodes', { params: { model: 'no-such-model' } });
  expect(otherModel.status).toBe(200);
  expect(otherModel.body.durations).toEqual([]);
  const sameModel = await api.get('/api/analytics/durations/nodes', { params: { model: 'claude-opus-4-8' } });
  expect(sameModel.body.durations.map((row: { key: string }) => row.key)).toEqual([nodeId]);

  const outcomes = await api.get('/api/analytics/outcomes/nodes');
  expect(outcomes.status).toBe(200);
  const outcomesByNode = Object.fromEntries(outcomes.body.outcomes.map((row: { node_id: string }) => [row.node_id, row]));
  expect(outcomesByNode[nodeId].choice_counts).toEqual({ 'already-done': 1 });
  expect(outcomesByNode[nodeId].attempt_failures).toBe(0);
});
