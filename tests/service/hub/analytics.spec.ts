import { expect, test } from '../../harness/fixtures';

// Transcript-event re-derive over a real hub: a segment-scoped force, and a chunk-scoped, bounded call that converges to
// `remaining` 0 over repeated calls. The analytics routes read tenant-wide aggregates, so each test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

/** One final transcript segment holding a single `asst` turn, on the lane `(chunkId, nd_build, 1)`. */
function segment(chunkId: string, segmentId: string, seq: number) {
  return {
    seq,
    segment_id: segmentId,
    chunk_id: chunkId,
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 1,
    turn_range_start: 0,
    turn_range_end: 0,
    final: true,
    normalizer_version: 'v1',
    harness_version: 'claude-code-1.0',
    turns: [{ index: 0, kind: 'asst', timestamp: null, text: 'hi', tool: null, thinking_redacted: false, sidechain: null, truncated: false }],
  };
}

test('test_a_segment_scoped_call_forces_that_one_segment', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('re-derive segment scope');
  const runner = await fleet('r-segment');
  const ack = await runner.post('/api/fleet/transcripts', { data: { records: [segment(chunk.chunk_id, 'sg_1', 1)] } });
  expect(ack.status).toBe(200);

  const resp = await api.post('/api/analytics/re-derive', { data: { segment_id: 'sg_1' } });

  expect(resp.status).toBe(200);
  expect(resp.body).toEqual({ derived: 1, remaining: 0, not_visible: false });
});

test('test_a_chunk_scoped_bounded_call_converges_over_repeated_calls', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('re-derive chunk scope');
  const runner = await fleet('r-chunk');
  const records = [1, 2, 3].map((i) => segment(chunk.chunk_id, `sg_${i}`, i));
  const ack = await runner.post('/api/fleet/transcripts', { data: { records } });
  expect(ack.status).toBe(200);

  const call = { chunk_id: chunk.chunk_id, limit: 2 };
  const first = await api.post('/api/analytics/re-derive', { data: call });
  expect(first.status).toBe(200);
  expect(first.body).toEqual({ derived: 2, remaining: 1, not_visible: false });

  const second = await api.post('/api/analytics/re-derive', { data: call });
  expect(second.status).toBe(200);
  expect(second.body).toEqual({ derived: 1, remaining: 0, not_visible: false });

  const third = await api.post('/api/analytics/re-derive', { data: call });
  expect(third.status).toBe(200);
  expect(third.body).toEqual({ derived: 0, remaining: 0, not_visible: false });
});

test('test_a_call_naming_both_a_segment_and_a_chunk_is_422', async ({ api, issue }) => {
  const { chunk } = await issue('re-derive mutually exclusive scope');

  const resp = await api.post('/api/analytics/re-derive', { data: { segment_id: 'sg_1', chunk_id: chunk.chunk_id } });

  expect(resp.status).toBe(422);
});
