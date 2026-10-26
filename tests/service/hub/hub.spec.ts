import { expect, test } from '../../harness/fixtures';

// The hub's HTTP API from outside the process, the runner mocked: claim and completion, stale-epoch rejection, queue
// shaping, the SSE contract and live fan-out, runner subscription usage, and checks-gate authorization. Route-token and
// produces enforcement run under their own profiles, in the folders beside this one.
// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.
test.use({ isolation: 'fresh-tenant' });

const DELIVER = {
  executor: 'hub',
  action: 'pass',
  judgement: {
    choices: { success: { description: 'Delivered.', to: 'done' }, failure: { description: 'Failed to deliver.', to: 'build' } },
  },
};
const REVIEW = {
  executor: 'runner',
  prompt: '# review',
  session: 'fresh',
  judgement: { prompt: '# judge', choices: { pass: { description: 'clean', to: 'deliver' } } },
  retries: { max: 1, exhausted: 'escalate' },
};

/** A scripted `default-delivery` graph, build then review then deliver, named so the hub's lazy default reuses it. */
const DELIVERY_GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'review' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    review: REVIEW,
    deliver: DELIVER,
  },
});

/** A source graph whose `build` offers a cross-graph choice, and its target graph. */
const MIGRATION_SOURCE = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      judgement: {
        prompt: '# judge',
        choices: {
          migrate: { description: 'Hand off to triage.', to: 'graph:triage' },
          fail: { description: 'Retry.', to: 'build' },
        },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});
const MIGRATION_TARGET = JSON.stringify({
  name: 'triage',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# triage',
      judgement: {
        prompt: '# judge',
        choices: { pass: { description: 'Done.', to: 'done' }, fail: { description: 'Retry.', to: 'build' } },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

/** The default `warn` mode: the build node declares `produces: [notes]`. */
const PRODUCES_GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      produces: ['notes'],
      judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'review' } } },
      retries: { max: 1, exhausted: 'escalate' },
    },
    review: REVIEW,
    deliver: DELIVER,
  },
});
const FALLBACK_NOTES = [{ name: 'notes', kind: 'asset', content: 'assessment fallback', attached: false }];

/** A `requires_checks` pass on `build`; `fail` is ungated and routes back to build. */
const CHECKS_GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: '# build',
      checks: ['mise run test'],
      judgement: {
        prompt: '# judge',
        choices: {
          pass: { description: 'green', to: 'deliver', requires_checks: true },
          fail: { description: 'red', to: 'build' },
        },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
    deliver: DELIVER,
  },
});
const RED_CHECK = [{ command: 'mise run test', passed: false }];
const GREEN_CHECK = [{ command: 'mise run test', passed: true }];

/** A whole-second UTC instant, the form the hub echoes back unchanged. */
const wholeSecondNow = (): string => new Date(Math.floor(Date.now() / 1000) * 1000).toISOString().replace('.000Z', '+00:00');

const ROSTER = [{ slug: 'probe', name: 'Probe', provider: 'none-such' }];
const PROBE_ROW = { slug: 'probe', name: 'Probe', sampled_at: null, windows: [], condition: null, miss_reason: null, missed_at: null };

test.describe('on a fresh tenant', () => {
  test('test_claim_and_completion_advance_the_chunk_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: DELIVERY_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('claim + complete');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-claim');

    expect((await runner.drive('register')).body.status).toBe(201);
    const peek = await runner.drive('peek');
    expect(peek.body.response.entries.some((entry) => entry.chunk_id === chunkId)).toBe(true);

    const claim = await runner.drive('claim', { chunk_id: chunkId });
    expect(claim.body.claimed).toBe(true); // the hub handed back the first node envelope over the wire
    expect((await api.get(`/api/chunks/${chunkId}`)).body.status).toBe('running');

    const complete = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass' });
    expect(complete.body.response?.outcome, JSON.stringify(complete.body)).toBe('next'); // build -> review
    const detail = (await api.get(`/api/chunks/${chunkId}`)).body;
    expect(detail.status).toBe('running');
    expect(detail.current_node_id, JSON.stringify(detail)).not.toBe(claim.body.from_node_id); // moved off build onto review
    expect(detail.latest_epoch ?? 0).toBeGreaterThanOrEqual(1);
  });

  test('test_stale_epoch_completion_is_rejected_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: DELIVERY_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('stale epoch');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-stale');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);
    const before = (await api.get(`/api/chunks/${chunkId}`)).body.current_node_id;

    // Arm the runner to fence its completion with a stale (held epoch - 1) epoch.
    expect((await runner.lever('stale_epoch', { chunk_id: chunkId })).status).toBe(200);
    const out = await runner.drive('complete', { chunk_id: chunkId, choice: 'pass' });

    expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('failure'); // the hub fenced the zombie
    expect((out.body.response?.detail ?? '').toLowerCase()).toContain('stale');
    expect((await api.get(`/api/chunks/${chunkId}`)).body.current_node_id).toBe(before);
  });

  test('test_queue_shaping_group_and_reorder_reflected_in_peek', async ({ api, issue }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: DELIVERY_GRAPH } })).status).toBe(201);
    const filed = [];
    for (const title of ['A — stays', 'B — survivor', 'C — merged into B']) {
      const { chunk } = await issue(title);
      expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
      filed.push(chunk.chunk_id);
    }
    const [chunkA, chunkB, chunkC] = filed;

    // Group C into B: the survivor absorbs the union of work refs.
    const grouped = await api.post(`/api/chunks/${chunkB}/group`, { data: { merge_chunk_ids: [chunkC] } });
    expect(grouped.status, JSON.stringify(grouped.body)).toBe(200);
    expect(grouped.body.work_refs).toHaveLength(2);

    // Move the survivor to the top by the whole-order replace; the read reflects both actions.
    expect((await api.put('/api/queue', { data: { chunk_ids: [chunkB, chunkA] } })).status).toBe(200);
    const queue = await api.get('/api/queue');
    expect(queue.body.entries.map((entry: { chunk_id: string }) => entry.chunk_id)).toEqual([chunkB, chunkA]); // C merged away
  });

  test('test_a_fresh_migration_publishes_queue_changed_to_a_live_subscriber', async ({ api, issue, mockRunner, events }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: MIGRATION_SOURCE } })).status).toBe(201);
    expect((await api.post('/api/graphs', { data: { definition_yaml: MIGRATION_TARGET } })).status).toBe(201);
    const { chunk } = await issue('migrate me');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-migrate');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);

    // Arm the duplicate-delivery lever: one drive submits the byte-identical completion twice; exactly one queue-changed
    // must come out.
    expect((await runner.lever('replay', { chunk_id: chunkId })).status).toBe(200);

    // Subscribe before the act, so what arrives after is fan-out and not replay.
    const subscription = await events.open({ barrier: true });
    const drove = await runner.drive('complete', { chunk_id: chunkId, choice: 'migrate' });
    expect(drove.body.response?.outcome, JSON.stringify(drove.body)).toBe('migrated');
    expect(drove.body.replayed?.response?.outcome, JSON.stringify(drove.body)).toBe('migrated'); // idempotent, not an error
    const live = await subscription.barrier();
    expect(live.filter((frame) => frame.event === 'queue-changed')).toHaveLength(1);

    // ...and the chunk really is re-queued, under the target graph.
    const detail = (await api.get(`/api/chunks/${chunkId}`)).body;
    const graphs = Object.fromEntries(((await api.get('/api/graphs')).body as { graph_id: string; name: string }[]).map((graph) => [graph.graph_id, graph.name]));
    expect(detail.status, JSON.stringify(detail)).toBe('ready');
    expect(graphs[detail.graph_id]).toBe('triage');
  });

  test('test_runner_registers_and_reads_its_pause_brake', async ({ api, mockRunner }) => {
    const runner = await mockRunner('runner-brake');
    const registered = await runner.drive('register');
    expect(registered.body.response.runner_id).toBe(runner.runnerId);

    const listed = await api.get('/api/runners');
    expect((listed.body.runners as { runner_id: string }[]).some((entry) => entry.runner_id === runner.runnerId)).toBe(true);
    // The operator flips the pause brake; the hub's registry reflects it.
    expect((await api.post(`/api/runners/${runner.runnerId}/pause`, { data: { by: 'operator' } })).status).toBe(200);
    const view = (await api.get(`/api/runners/${runner.runnerId}`)).body;
    expect(view.runner_name).toBe('runner-brake');
    expect(view.hub_paused).toBe(true);
    // The runner's own brake is a separate field the hub only reads; the operator's brake must not appear to set it.
    expect(view.locally_paused).toBe(false);
  });

  test('test_external_subscription_usage_round_trips_a_slug_and_rejects_a_non_string_slug', async ({ api, mockRunner }) => {
    const sampledAt = wholeSecondNow();
    const runner = await mockRunner('runner-subscriptions');
    await runner.drive('register');
    const valid = await runner.drive('report-external-usage', { slug: 'openai', name: 'OpenAI', sampled_at: sampledAt, windows: [] });
    expect(valid.body.response.applied).toEqual([1]);

    // `raw_slug` is the mock's malformed-slug lever: it pushes the value unnarrowed so the real hub's own intake rejects it.
    const invalid = await runner.drive('report-external-usage', { raw_slug: 123, sampled_at: sampledAt, windows: [] });
    expect(invalid.body.response.rejected).toEqual([2]);

    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toEqual([
      { slug: 'openai', name: 'OpenAI', sampled_at: sampledAt, windows: [], condition: null, miss_reason: null, missed_at: null },
    ]);
  });

  test('test_external_subscription_usage_miss_lands_as_a_lapsed_condition_over_the_wire', async ({ api, mockRunner }) => {
    const missedAt = wholeSecondNow();
    const runner = await mockRunner('runner-lapsed');
    await runner.drive('register');
    const driven = await runner.drive('report-external-usage-miss', { slug: 'openai', name: 'OpenAI', missed_at: missedAt, reason: 'credential_lapsed' });
    expect(driven.body.response.applied).toEqual([1]);

    // `raw_slug` is the mock's malformed-slug lever, mirroring the sampled test above.
    const invalid = await runner.drive('report-external-usage-miss', { raw_slug: 123, missed_at: missedAt, reason: 'credential_lapsed' });
    expect(invalid.body.response.rejected).toEqual([2]);

    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toEqual([
      { slug: 'openai', name: 'OpenAI', sampled_at: null, windows: [], condition: 'credential_lapsed', miss_reason: 'credential_lapsed', missed_at: missedAt },
    ]);
  });

  test('test_a_declared_roster_shows_a_never_sampled_member_over_the_wire', async ({ api, mockRunner }) => {
    const runner = await mockRunner('runner-roster');
    await runner.drive('register', { subscriptions: ROSTER });

    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toEqual([PROBE_ROW]);
  });

  test('test_reregistering_a_declared_roster_without_a_slug_removes_it_over_the_wire', async ({ api, mockRunner }) => {
    const runner = await mockRunner('runner-roster-2');
    await runner.drive('register', { subscriptions: ROSTER });
    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toHaveLength(1);

    await runner.drive('register', { subscriptions: [] });
    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toEqual([]);
  });

  test('test_a_registration_without_a_roster_keeps_the_legacy_path_over_the_wire', async ({ api, mockRunner }) => {
    const runner = await mockRunner('runner-no-roster');
    await runner.drive('register');

    expect((await api.get(`/api/runners/${runner.runnerId}`)).body.subscriptions).toEqual([]);
  });

  test('test_fallback_only_completion_is_accepted_under_warn_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: PRODUCES_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('produces warn');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-produces-warn');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);

    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass', artifacts: FALLBACK_NOTES });
    expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('next'); // applied despite no attachment
  });

  test('test_checks_gate_fences_a_red_gated_pass_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: CHECKS_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('checks gate red');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-checks-red');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
    const before = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id;

    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass', check_results: RED_CHECK });

    expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('failure');
    expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id).toBe(before);
  });

  test('test_a_green_gated_pass_applies_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: CHECKS_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('checks gate green');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-checks-green');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);

    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass', check_results: GREEN_CHECK });
    expect(out.body.response?.outcome, JSON.stringify(out.body)).not.toBe('failure'); // advanced to deliver
  });

  test('test_a_red_check_through_a_non_gated_fail_applies_over_the_wire', async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: CHECKS_GRAPH } })).status).toBe(201);
    const { chunk } = await issue('checks gate non-gated fail');
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-checks-fail');
    await runner.drive('register');
    expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);

    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'fail', check_results: RED_CHECK });
    expect(out.body.response?.outcome, JSON.stringify(out.body)).not.toBe('failure'); // fail -> build, a normal transition
  });
});

// Neither of these posts a graph or touches a tenant-wide resource, so they share the common tenant.
test.describe('on the common tenant', () => {
  test.use({ isolation: 'common' });

  test('test_sse_stream_serves_the_eventsource_contract', async ({ events, issue }) => {
    await issue('an event'); // a chunk-changed event enters the broker's buffer

    const subscription = await events.open();

    expect(subscription.contentType).toMatch(/^text\/event-stream/);
    expect(subscription.openingComment).toMatch(/^: blizzard hub event stream/);
  });

  test('test_chunk_pause_field_reflects_the_operator_chunk_brake', async ({ api, issue }) => {
    const { chunk } = await issue('pause over the wire');
    const chunkId = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const summary = async (): Promise<Record<string, unknown>> => {
      const listed = (await api.get('/api/chunks')).body.chunks as Record<string, unknown>[];
      const card = listed.find((candidate) => candidate.chunk_id === chunkId);
      if (card === undefined) throw new Error(`chunk ${chunkId} is not in the chunk list`);
      return card;
    };

    expect((await api.get(`/api/chunks/${chunkId}`)).body.pause).toBeNull();
    // The card is a passive status view: no pause fact on the summary.
    expect(await summary()).not.toHaveProperty('paused');

    const paused = await api.post(`/api/chunks/${chunkId}/pause`, { data: { by: 'operator' } });
    expect(paused.status, JSON.stringify(paused.body)).toBe(202);
    const detail = (await api.get(`/api/chunks/${chunkId}`)).body;
    expect(detail.status).toBe('paused');
    expect(detail.pause.by).toBe('operator');
    expect(detail.pause.set_at).toBeTruthy();
    expect((await summary()).status).toBe('paused'); // the card still reflects the pause as a status
    expect(await summary()).not.toHaveProperty('paused');

    const resumed = await api.post(`/api/chunks/${chunkId}/resume`, { data: { by: 'operator' } });
    expect(resumed.status, JSON.stringify(resumed.body)).toBe(202);
    expect((await api.get(`/api/chunks/${chunkId}`)).body.pause).toBeNull();
  });
});

// The hub is stopped, so the test owns the process.
test.describe('on a fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  /**
   * SIGTERM sets the hub's shutdown flag synchronously, ahead of uvicorn's own graceful drain (bounded at 5 s), so the
   * process exits well inside that bound even with an SSE client still connected, rather than riding out the drain.
   */
  test('test_sigterm_returns_promptly_with_a_client_parked_on_the_stream', async ({ events, freshHub }) => {
    await events.open(); // returns once the reserved comment has arrived: the subscriber is live

    const started = Date.now();
    await freshHub.stop();

    expect(Date.now() - started, 'the hub took 2s or more to exit with a client parked on the stream').toBeLessThan(2000);
  });
});
