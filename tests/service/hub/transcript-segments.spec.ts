import { expect, test, type TranscriptSegmentRecord } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.

// The hub's fleet transcript ingest and the chunk and lease transcript reads over a real hub. Every read is scoped to
// the test's own chunk, but each test ships through `fleet`, which refuses below a tenant of its own.
test.use({ isolation: 'fresh-tenant' });

// `RECORD_MAX_BYTES`, in `blizzard.hub.domain.observability.transcripts`: the largest record the hub stores whole.
const RECORD_MAX_BYTES = 10 * 1024 * 1024;

/** One final record on `(chunkId, nd_build, 1)`'s lane: a single `asst` turn. */
function record(chunkId: string, seq: number, text = 'hi'): TranscriptSegmentRecord {
  return {
    seq,
    segment_id: 'sg_1',
    chunk_id: chunkId,
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 1,
    turn_range_start: 0,
    turn_range_end: 0,
    final: true,
    normalizer_version: 'v1',
    harness_version: 'claude-code-1.0',
    turns: [{ index: 0, kind: 'asst', timestamp: null, text, tool: null, thinking_redacted: false, sidechain: null, truncated: false }],
  };
}

test('test_ingest_and_read_back_round_trip_over_the_wire', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('transcript segments');
  const runner = await fleet('fleet-driver');

  const ack = await runner.post('/api/fleet/transcripts', { data: { records: [record(chunk.chunk_id, 1)] } });
  expect(ack.status).toBe(200);
  expect(ack.body.applied).toEqual([1]);

  const index = await api.get(`/api/chunks/${chunk.chunk_id}/transcripts`);
  expect(index.status).toBe(200);
  expect(index.body.segments).toHaveLength(1);
  expect(index.body.segments[0].segment_id).toBe('sg_1');
  expect(index.body.segments[0].final).toBe(true);

  const content = await api.get(`/api/chunks/${chunk.chunk_id}/transcripts/sg_1`);
  expect(content.status).toBe(200);
  expect(content.body.turns.map((turn: { text: string }) => turn.text)).toEqual(['hi']);
});

test('test_a_segment_is_not_readable_through_another_chunks_path', async ({ api, fleet, issue }) => {
  const { chunk: owning } = await issue('owning chunk');
  const { chunk: other } = await issue('other chunk');
  const runner = await fleet('fleet-driver');
  const ack = await runner.post('/api/fleet/transcripts', { data: { records: [record(owning.chunk_id, 1)] } });
  expect(ack.status).toBe(200);

  expect((await api.get(`/api/chunks/${other.chunk_id}/transcripts/sg_1`)).status).toBe(404);
  expect((await api.get(`/api/chunks/${owning.chunk_id}/transcripts/sg_1`)).status).toBe(200);
});

test('test_a_cap_rejected_record_re_offered_under_a_fresh_seq_is_re_adjudicated', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('re-adjudicated segment');
  const runner = await fleet('fleet-driver');

  const ack = await runner.post('/api/fleet/transcripts', {
    data: { records: [record(chunk.chunk_id, 1, 'x'.repeat(RECORD_MAX_BYTES + 1))] },
  });
  expect(ack.status).toBe(200);
  expect(ack.body.capped).toEqual([1]);
  const capped = await api.get(`/api/chunks/${chunk.chunk_id}/transcripts`);
  expect(capped.body.segments[0].truncated).toBe(true);

  const retry = await runner.post('/api/fleet/transcripts', { data: { records: [record(chunk.chunk_id, 99)] } });
  expect(retry.body.applied).toEqual([99]);
  const content = await api.get(`/api/chunks/${chunk.chunk_id}/transcripts/sg_1`);
  expect(content.body.turns.map((turn: { text: string }) => turn.text)).toEqual(['hi']);
  const readmitted = await api.get(`/api/chunks/${chunk.chunk_id}/transcripts`);
  expect(readmitted.body.segments).toHaveLength(1);
  expect(readmitted.body.segments[0].truncated).toBe(false);
});

test('test_an_enrolled_runner_reads_back_the_lease_segments_it_shipped', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('lease transcript read');
  const runner = await fleet('r1');
  const enroll = await api.post(`/api/runners/${runner.runnerId}/enrollments`);
  expect(enroll.status).toBe(201);
  // The enrollment stops the token the add minted from resolving: every later call carries the enrolled one.
  const headers = { Authorization: `Bearer ${enroll.body.token}` };
  const register = await runner.post('/api/fleet/runners', {
    data: { name: 'r1', workspace_id: 'ws-1', capabilities: [{ harness_id: 'claude', default: true }] },
    headers,
  });
  expect(register.status).toBe(201);

  const ack = await runner.post('/api/fleet/transcripts', { data: { records: [record(chunk.chunk_id, 1)] }, headers });
  expect(ack.status).toBe(200);

  const resp = await runner.get(`/api/fleet/chunks/${chunk.chunk_id}/transcript-segments`, {
    params: { node_id: 'nd_build', epoch: 1 },
    headers,
  });
  expect(resp.status).toBe(200);
  expect(resp.body.chunk_id).toBe(chunk.chunk_id);
  expect(resp.body.node_id).toBe('nd_build');
  expect(resp.body.epoch).toBe(1);
  expect(resp.body.truncated).toBe(false);
  expect(resp.body.turns.map((turn: { text: string }) => turn.text)).toEqual(['hi']);
});
