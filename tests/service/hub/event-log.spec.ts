import { expect, test } from '../../harness/fixtures';

async function eventFeed(api: { get: (route: string) => Promise<{ status: number; body: { events: { kind: string; [key: string]: unknown }[] } }> }) {
  const answer = await api.get('/api/events');
  expect(answer.status).toBe(200);
  return answer.body.events;
}

// The server timestamps activity at microsecond precision. Advance that exact wire value without a Date round-trip,
// whose millisecond precision could leave an inclusive `since` at or before the promoted row.
function afterMicrosecond(at: string): string {
  const match = /^(.*?)(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(at);
  if (match === null) throw new Error(`unexpected activity timestamp: ${at}`);
  const [, seconds, fraction = '', zone] = match;
  const next = Number(fraction.padEnd(6, '0')) + 1;
  if (next === 1_000_000) return `${new Date(Date.parse(`${seconds}${zone}`) + 1_000).toISOString().slice(0, 19)}.000000+00:00`;
  return `${seconds}.${String(next).padStart(6, '0')}${zone}`;
}

test.describe('tenant event log', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('test_a_driven_event_folds_into_the_log_and_fans_out_over_sse_exactly_once', async ({ api, events, issue, mockRunner }) => {
    const { chunk } = await issue('surface a worker-lost event');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const runner = await mockRunner('event-reporter');
    expect((await runner.drive('register')).body.status).toBe(201);

    const subscription = await events.open({ barrier: true });
    const drove = await runner.drive('report-event', {
      severity: 'critical', kind: 'worker-lost', message: 'worker exited without a session-end',
      chunk_id: chunkId, node_name: 'build', detail: { via: 'advance', reason: 'failed' },
    });
    expect(drove.status).toBe(200);
    expect(drove.body.drove).toBe(true);
    expect(drove.body.status).toBe(200);
    const frames = await subscription.barrier();
    expect(frames.filter((frame) => frame.event === 'event-logged')).toHaveLength(1);
    expect(frames.filter((frame) => frame.event === 'chunk-changed')).toHaveLength(0);

    const feed = await eventFeed(api);
    const lost = feed.filter((row: { kind: string }) => row.kind === 'worker-lost');
    expect(lost).toHaveLength(1);
    expect(lost[0]).toMatchObject({ severity: 'critical', chunk_id: chunkId, node_name: 'build', detail: { via: 'advance', reason: 'failed' } });
  });

  test('test_a_replayed_event_seq_folds_exactly_once', async ({ api, fleet }) => {
    const runner = await fleet('fleet-driver');
    const batch = {
      facts: [{ seq: 1, kind: 'event.recorded', payload: { severity: 'warning', kind: 'command-failed', message: 'git push failed' } }],
    };
    expect((await runner.post('/api/fleet/events', { data: batch })).status).toBe(200);
    expect((await runner.post('/api/fleet/events', { data: batch })).status).toBe(200);
    const failed = (await eventFeed(api)).filter((row: { kind: string }) => row.kind === 'command-failed');
    expect(failed).toHaveLength(1);
    expect(failed[0]).toMatchObject({ severity: 'warning', chunk_id: null });
  });

  test('test_activity_backfill_merges_several_cause_families_bounded_and_newest_first', async ({ api, issue, mockRunner }) => {
    const graph = `name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: '# build'
    judgement:
      prompt: '# judge'
      choices:
        pass: {description: green, to: review}
    retries: {max: 1, exhausted: escalate}
  review:
    executor: runner
    prompt: '# review'
    session: fresh
    judgement:
      prompt: '# judge'
      choices:
        pass: {description: clean, to: deliver}
    retries: {max: 1, exhausted: escalate}
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success: {description: Delivered., to: done}
        failure: {description: Failed to deliver., to: build}
`;
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
    const { chunk } = await issue('several cause families');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const promotedFeed = await api.get('/api/activity');
    expect(promotedFeed.status).toBe(200);
    const promoted = promotedFeed.body.activity.find((row: { chunk_id: string; cause: string }) => row.chunk_id === chunkId && row.cause === 'promoted');
    expect(promoted).toBeDefined();
    const since = afterMicrosecond(promoted.at);

    const runner = await mockRunner('activity-reporter');
    expect((await runner.drive('register')).body.status).toBe(201);
    const claim = await runner.drive('claim', { chunk_id: chunkId });
    expect(claim.body.claimed).toBe(true);
    const claimedFeed = await api.get('/api/activity');
    const claimed = claimedFeed.body.activity.find((row: { chunk_id: string; cause: string }) => row.chunk_id === chunkId && row.cause === 'claimed');
    expect(claimed).toBeDefined();
    expect(claimed.at > since).toBe(true);

    const complete = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass' });
    expect(complete.body.response?.outcome).toBe('next');
    expect((await runner.drive('escalate', { chunk_id: chunkId })).body.drove).toBe(true);
    const reported = await runner.drive('report-event', {
      severity: 'warning', kind: 'worker-context-warned', message: 'a probed operational event', chunk_id: chunkId,
    });
    expect(reported.body.status).toBe(200);
    expect((await runner.drive('pause', { by: 'operator' })).body.status).toBe(200);
    expect((await api.post(`/api/chunks/${chunkId}/pause`, { data: { by: 'operator' } })).status).toBe(202);
    expect((await api.post(`/api/chunks/${chunkId}/resume`, { data: { by: 'operator' } })).status).toBe(202);

    const response = await api.get('/api/activity');
    expect(response.status).toBe(200);
    const feed = response.body.activity;
    const fields = new Set(['type', 'key', 'at', 'chunk_id', 'status', 'prev_status', 'node', 'prev_node',
      'runner_id', 'runner_name', 'cause', 'graph_id', 'severity', 'kind', 'by', 'reason']);
    for (const row of feed) {
      expect(Object.keys(row).every((field) => fields.has(field))).toBe(true);
      for (const field of ['type', 'key', 'at']) {
        expect(row[field]).toEqual(expect.any(String));
        expect(row[field].length).toBeGreaterThan(0);
      }
    }
    const causes = new Set(feed.filter((row: { chunk_id: string; type: string }) => row.chunk_id === chunkId && row.type === 'chunk-changed')
      .map((row: { cause: string }) => row.cause));
    for (const cause of ['minted', 'promoted', 'claimed', 'node-completed', 'escalated', 'paused', 'resumed']) expect(causes.has(cause)).toBe(true);
    const eventRows = feed.filter((row: { type: string; kind: string }) => row.type === 'event-logged' && row.kind === 'worker-context-warned');
    expect(eventRows).toHaveLength(1);
    expect(eventRows[0]).toMatchObject({ severity: 'warning', chunk_id: chunkId });
    const runnerRows = feed.filter((row: { type: string; kind: string }) => row.type === 'runner-changed' && row.kind === 'locally-paused');
    expect(runnerRows).toHaveLength(1);
    expect(runnerRows[0].by).toBe('operator');

    const ats = feed.map((row: { at: string }) => row.at);
    expect(ats).toEqual([...ats].sort().reverse());
    expect(feed.length).toBeLessThanOrEqual(200);
    const limited = await api.get('/api/activity', { params: { limit: 1 } });
    expect(limited.status).toBe(200);
    expect(limited.body.activity).toHaveLength(1);
    expect(limited.body.activity[0].at).toBe(ats[0]);

    const narrowed = await api.get('/api/activity', { params: { since } });
    expect(narrowed.status).toBe(200);
    const narrowedCauses = new Set(narrowed.body.activity.filter((row: { chunk_id: string; type: string }) => row.chunk_id === chunkId && row.type === 'chunk-changed')
      .map((row: { cause: string }) => row.cause));
    expect(narrowedCauses.has('minted')).toBe(false);
    expect(narrowedCauses.has('promoted')).toBe(false);
    for (const cause of ['claimed', 'node-completed', 'escalated', 'paused', 'resumed']) expect(narrowedCauses.has(cause)).toBe(true);
    expect(narrowed.body.activity.some((row: { type: string; kind: string }) => row.type === 'event-logged' && row.kind === 'worker-context-warned')).toBe(true);
    expect(narrowed.body.activity.some((row: { type: string; kind: string }) => row.type === 'runner-changed' && row.kind === 'locally-paused')).toBe(true);
  });
});

for (const setting of ['OTEL_EXPORTER_OTLP_PROTOCOL', 'OTEL_EXPORTER_OTLP_TRACES_PROTOCOL']) {
  test.describe(`tracing ${setting}`, () => {
    test.describe.configure({ timeout: 90_000 });
    test.use({ isolation: 'fresh-hub', hubEnv: { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://127.0.0.1:1', [setting]: 'grpc' } });

    test(`test_a_grpc_tracing_protocol_leaves_the_hub_serving_with_one_rejection[${setting}]`, async ({ api }) => {
      expect((await api.get('/api/health')).status).toBe(200);
      expect((await eventFeed(api)).filter((row: { kind: string }) => row.kind === 'trace-config-rejected')).toHaveLength(0);
      const response = await api.get('/api/admin/events');
      expect(response.status).toBe(200);
      const rejected = response.body.filter((row: { kind: string }) => row.kind === 'trace-config-rejected');
      expect(rejected).toHaveLength(1);
      expect(rejected[0]).toMatchObject({ severity: 'warning', detail: { setting, value: 'grpc' } });
    });
  });
}
