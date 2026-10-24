import { expect, test } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.

// The analytics events and counts routes over a real hub: both encodings serve the same derived events in the same
// order, and a JSON page bounded below the total still covers it exactly once through its cursor. The routes read
// tenant-wide aggregates, so each test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

/** One final segment of three tool turns: a file read, a skill, and a sub-agent — one derived event each. */
function toolSegment(chunkId: string) {
  const tool = (index: number, name: string, input: Record<string, unknown>) => ({
    index,
    kind: 'tool',
    timestamp: null,
    text: '',
    tool: {
      name,
      input,
      input_unparsed: null,
      input_shape: 'object',
      tool_use_id: `t${index}`,
      output: null,
      output_truncated: false,
    },
    thinking_redacted: false,
    sidechain: null,
    truncated: false,
  });
  return {
    seq: 1,
    segment_id: 'sg_1',
    chunk_id: chunkId,
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 1,
    turn_range_start: 0,
    turn_range_end: 2,
    final: true,
    normalizer_version: 'claude-code-jsonl/2',
    harness_version: 'claude-code-1.0',
    turns: [tool(0, 'Read', { file_path: 'src/a.py' }), tool(1, 'Skill', { skill: 'wf-commit' }), tool(2, 'Agent', { subagent_type: 'explorer' })],
  };
}

test('test_both_encodings_serve_the_same_derived_events_in_the_same_order', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('analytics events over a real hub');
  const runner = await fleet('r-events');
  const push = await runner.post('/api/fleet/transcripts', { data: { records: [toolSegment(chunk.chunk_id)] } });
  expect(push.status).toBe(200);
  const derived = await api.post('/api/analytics/re-derive', { data: { chunk_id: chunk.chunk_id } });
  expect(derived.status).toBe(200);
  expect(derived.body.derived).toBe(1);

  const json = await api.get('/api/analytics/events', { params: { kind: 'skill_invocation' } });
  expect(json.status).toBe(200);
  expect(json.body.events.map((event: { subject: string }) => event.subject)).toEqual(['wf-commit']);

  const ndjson = await api.get('/api/analytics/events/ndjson');
  expect(ndjson.status).toBe(200);
  expect(ndjson.headers['content-type']).toMatch(/^application\/x-ndjson/);
  const lines = String(ndjson.body)
    .trim()
    .split('\n')
    .filter((line) => line !== '');
  expect(lines).toHaveLength(3);
});

test('test_a_json_page_bounded_below_the_total_covers_it_exactly_once_via_its_cursor', async ({ api, fleet, issue }) => {
  const { chunk } = await issue('analytics paging over a real hub');
  const runner = await fleet('r-paging');
  const push = await runner.post('/api/fleet/transcripts', { data: { records: [toolSegment(chunk.chunk_id)] } });
  expect(push.status).toBe(200);
  const derived = await api.post('/api/analytics/re-derive', { data: { chunk_id: chunk.chunk_id } });
  expect(derived.status).toBe(200);

  const seen: number[] = [];
  let cursor: string | null = null;
  const pageOf = (after: string | null) => api.get('/api/analytics/events', { params: { limit: 1, ...(after === null ? {} : { cursor: after }) } });
  // A generous bound on the pages of a 3-row set.
  for (let page = 0; page < 10; page++) {
    const answer = await pageOf(cursor);
    expect(answer.status).toBe(200);
    expect(answer.body.events).toHaveLength(1);
    seen.push(answer.body.events[0].id);
    cursor = answer.body.next_cursor;
    if (cursor === null) break;
  }

  expect(cursor).toBeNull();
  expect(seen).toHaveLength(3);
  expect(new Set(seen).size).toBe(3);
});
