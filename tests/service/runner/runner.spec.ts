import { expect, type MockChunkSpec, type MockHub, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A real runner host stepped against the test's own mock hub, whose levers manufacture the rare states a runner must
// survive: an unreachable hub, a dropped ack, a stale envelope, a denied claim, a failing or slow transcript route, a
// usage limit, and a chunk stopped under a lease. Beside them, the host's own surfaces: a lease's transcript and
// artifacts read through its API, its event stream, and its shutdown with a stream parked or a trace export hung. Every
// wait on a stepped host is a step of it; every read is a public surface.

const REPO = 'toy-api';
const BRANCH = 'feature/runner-service';
const MAX_STEPS = 40;
const RUNNER = 'runner-local';

// The worker is a program: it works the branch in its worktree, commits, and pushes it to the world's origin.
const BUILD = [
  'import os, pathlib, subprocess',
  `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${REPO}')`,
  `subprocess.run(['git', '-C', repo, 'switch', '-C', '${BRANCH}'], check=True, capture_output=True)`,
  "(pathlib.Path(repo) / 'LANDED.md').write_text('landed by the mock worker\\n')",
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True, capture_output=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Worker', 'commit', '-m', 'feat: land a change'], check=True, capture_output=True)",
  `subprocess.run(['git', '-C', repo, 'push', 'origin', '${BRANCH}'], check=True, capture_output=True)`,
  '',
].join('\n');

/** A scripted build → deliver chunk; `build` overrides the build node's fields. */
function buildThenDeliver(build: Partial<MockChunkSpec['nodes'][string]> = {}): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: BUILD,
        judgement_prompt: "verdict('pass', 'the change is committed and green')\n",
        choices: [{ name: 'pass', description: 'committed and green', to: 'deliver' }],
        retries_max: 1,
        ...build,
      },
      deliver: {
        executor: 'hub',
        judgement: {
          choices: {
            landed: { description: 'Every repo merged cleanly.', to: 'done' },
            conflict: { description: 'A repo did not merge cleanly.', to: 'build' },
            failure: { description: 'A repo failed to land.', to: 'build' },
          },
        },
      },
    },
    work_refs: [{ source: 'mock', ref: `${REPO}/issues/1` }],
  };
}

/** Steps the host's sole runner until `reached` holds, failing after `MAX_STEPS` passes with the last state it saw. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    state = await read();
  }
  return state;
}

/** Steps the host's sole runner `count` times, each pass completing. */
async function steps(host: RunnerHost, count: number): Promise<void> {
  for (let pass = 0; pass < count; pass++) expect((await host.step()).outcome).toBe('completed');
}

async function seed(mockHub: MockHub, spec: MockChunkSpec = buildThenDeliver()): Promise<string> {
  const seeded = await mockHub.seed.chunk(spec);
  expect(seeded.status).toBe(201);
  return seeded.body.chunk_id;
}

/** The mock hub's chunk detail, read as the host's runner. */
async function chunk(mockHub: MockHub, host: RunnerHost, chunkId: string): Promise<Record<string, unknown>> {
  const detail = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  return detail.body as Record<string, unknown>;
}

async function chunkStatus(mockHub: MockHub, host: RunnerHost, chunkId: string): Promise<string> {
  return (await chunk(mockHub, host, chunkId)).status as string;
}

/** The runner's own `completion.submitted` fact for `chunkId`, as its facts feed holds it. */
async function completionFact(host: RunnerHost, chunkId: string) {
  return (await host.runners[RUNNER]!.facts()).body.items.find((fact) => fact.kind === 'completion.submitted' && fact.chunk_id === chunkId);
}

/** The requests the mock hub captured on `method path`. */
async function captured(mockHub: MockHub, method: string, path: string) {
  return (await mockHub.captured.read()).body.requests.filter((request) => request.method === method && request.path === path);
}

/** The hub's fleet view of the host's runner, read as that runner. */
async function runnerView(mockHub: MockHub, host: RunnerHost): Promise<Record<string, unknown>> {
  const view = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/runners/${host.runners[RUNNER]!.id}`);
  expect(view.status).toBe(200);
  return view.body as Record<string, unknown>;
}

/** The chunk's sole lease, through the runner's local API. */
async function soleLease(host: RunnerHost, chunkId: string) {
  const leases = (await host.runners[RUNNER]!.leases()).body.items.filter((lease) => lease.chunk_id === chunkId);
  expect(leases).toHaveLength(1);
  return leases[0]!;
}

/** The transcript turns the hub holds for a lease. */
async function hubTurns(mockHub: MockHub, host: RunnerHost, chunkId: string, lease: { node_id: string; epoch: number }): Promise<unknown[]> {
  const read = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/chunks/${chunkId}/transcript-segments`, { params: { node_id: lease.node_id, epoch: lease.epoch } });
  expect(read.status).toBe(200);
  return (read.body as { turns: unknown[] }).turns;
}

test('test_unreachable_hub_buffers_the_completion_then_lands_on_recovery', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = await seed(mockHub);
  const submissions = async (): Promise<number> => (await captured(mockHub, 'POST', '/api/fleet/events')).length;

  // The completion is recorded by the runner before the hub has taken it: pending, with the chunk still running.
  const pending = await stepUntil(runnerHost, () => completionFact(runnerHost, chunkId), (fact) => fact !== undefined);
  const seq = pending!.seq;
  expect(pending!.acked_at).toBeNull();
  expect(await chunkStatus(mockHub, runnerHost, chunkId)).not.toBe('done');

  // The hub goes unreachable: the runner attempts the push, and its completion stays pending under the same seq.
  await mockHub.levers.arm('unreachable');
  const before = await submissions();
  await stepUntil(runnerHost, submissions, (count) => count > before);
  const during = await completionFact(runnerHost, chunkId);
  expect(during).toMatchObject({ seq, acked_at: null });
  expect(await runner.bufferDepth()).toBeGreaterThanOrEqual(1);

  // The hub answers nothing during the outage, so the chunk is read once it is back and before the host steps again.
  await mockHub.levers.clear('unreachable');
  expect(await chunkStatus(mockHub, runnerHost, chunkId)).not.toBe('done');
  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');
  const acked = await completionFact(runnerHost, chunkId);
  expect(acked).toMatchObject({ seq });
  expect(acked!.acked_at).not.toBeNull();
  expect(await runner.bufferDepth()).toBe(0);

  // The worker's commit reached the origin on its work branch: the mock hub fakes deliver, so it is pushed, not merged.
  const head = await runnerHost.originHead(REPO, BRANCH);
  expect(head).toMatch(/^[0-9a-f]{40}$/);
  expect(head).not.toBe(await runnerHost.originHead(REPO, 'main'));
});

test('test_dropped_ack_reapplies_idempotently_through_to_done', async ({ mockHub, runnerHost }) => {
  const chunkId = await seed(mockHub);
  // The hub applies the first completion's write, then answers 503: the ack is lost though the transition landed.
  await mockHub.levers.arm('drop_ack', { chunk_id: chunkId, remaining: 1 });

  const pending = await stepUntil(runnerHost, () => completionFact(runnerHost, chunkId), (fact) => fact !== undefined);
  const seq = pending!.seq;
  expect(pending!.acked_at).toBeNull();

  await stepUntil(runnerHost, async () => (await completionFact(runnerHost, chunkId))?.acked_at ?? null, (ackedAt) => ackedAt !== null);
  expect(await completionFact(runnerHost, chunkId)).toMatchObject({ seq });
  expect((await mockHub.levers.list()).body.active.filter((lever) => lever.kind === 'drop_ack')).toEqual([]);
  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');

  // Done once: a further pass leaves it done, and the build node was worked by one lease, never re-run.
  await steps(runnerHost, 1);
  expect(await chunkStatus(mockHub, runnerHost, chunkId)).toBe('done');
  expect(await soleLease(runnerHost, chunkId)).toMatchObject({ node_name: 'build', state: 'closed' });
});

test('test_usage_limit_marks_the_subscription_and_parks_without_braking_or_failing_the_attempt', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = await seed(mockHub, buildThenDeliver({ prompt: 'usage_limited()\n' }));

  const hold = await stepUntil(runnerHost, async () => (await runner.status()).body.throttle?.floor.hold ?? null, (held) => held !== null);
  expect(hold).toMatchObject({ kind: 'mark', harness_id: 'claude_code' });
  const status = (await runner.status()).body;
  expect(status.pause.local).toBe(false);
  expect(status.pause.local_reason).toBeNull();
  expect((await soleLease(runnerHost, chunkId)).display_status).toBe('parked');

  await steps(runnerHost, 1);
  expect((await runnerView(mockHub, runnerHost)).locally_paused_reason ?? null).toBeNull();
  // Parked in place, not failed: the lease kept its claim, with no escalation and no epoch bump.
  expect(await chunk(mockHub, runnerHost, chunkId)).toMatchObject({ status: 'running', escalation: null, latest_epoch: 1 });
  expect((await soleLease(runnerHost, chunkId)).display_status).toBe('parked');
});

test('test_fill_absorbs_a_dependency_denial_then_claims_once_it_clears', async ({ mockHub, runnerHost }) => {
  const chunkId = await seed(mockHub);
  await mockHub.levers.arm('dependency_unmet', { chunk_id: chunkId, payload: { prerequisite_chunk_id: 'ch_prereq' } });

  await steps(runnerHost, 2);
  // Denied outright: each pass peeked the queue and found it blocked, so the chunk stays unclaimed and the passes complete.
  expect((await captured(mockHub, 'POST', '/api/fleet/queue/peek')).length).toBeGreaterThanOrEqual(2);
  expect(await chunkStatus(mockHub, runnerHost, chunkId)).toBe('ready');
  expect((await runnerHost.runners[RUNNER]!.leases()).body.items.filter((lease) => lease.chunk_id === chunkId)).toEqual([]);

  await mockHub.levers.clear('dependency_unmet', chunkId);
  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');
});

test('test_fill_reaches_past_a_marked_head_by_default_against_a_real_mock_hub', async ({ mockHub, runnerHost }) => {
  const blockedId = await seed(mockHub); // seeded first: the peek's marked head
  const openId = await seed(mockHub);
  await mockHub.levers.arm('dependency_unmet', { chunk_id: blockedId, payload: { prerequisite_chunk_id: 'ch_prereq' } });

  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, openId), (status) => status === 'running' || status === 'done');
  // Never even attempted: no claim names the marked head.
  expect(await chunkStatus(mockHub, runnerHost, blockedId)).toBe('ready');
  const claims = await captured(mockHub, 'POST', '/api/fleet/routes');
  expect(claims.filter((request) => (request.body as { chunk_id?: string } | null)?.chunk_id === blockedId)).toEqual([]);
});

test('test_fill_strict_holds_at_a_marked_head_against_a_real_mock_hub', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerConfig: { runner: [{ name: RUNNER, queue: { strict: true } }] } });
  const blockedId = await seed(mockHub); // seeded first: the peek's marked head
  const openId = await seed(mockHub);
  await mockHub.levers.arm('dependency_unmet', { chunk_id: blockedId, payload: { prerequisite_chunk_id: 'ch_prereq' } });

  await steps(host, 2);
  // Both passes peeked the queue and held at its marked head rather than reaching past it.
  expect((await captured(mockHub, 'POST', '/api/fleet/queue/peek')).length).toBeGreaterThanOrEqual(2);
  expect(await chunkStatus(mockHub, host, blockedId)).toBe('ready');
  expect(await chunkStatus(mockHub, host, openId)).toBe('ready');
  expect(await captured(mockHub, 'POST', '/api/fleet/routes')).toEqual([]);
});

test('test_transcript_route_failure_never_blocks_the_fact_lane', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerConfig: { transcripts: { ship: true } } });
  const chunkId = await seed(mockHub);
  await mockHub.levers.arm('unreachable_transcripts', { remaining: 10_000 });

  // The fact lane is unaffected by the transcript route's outage: the chunk lands, its completion acknowledged.
  await stepUntil(host, () => chunkStatus(mockHub, host, chunkId), (status) => status === 'done');
  expect((await completionFact(host, chunkId))?.acked_at).not.toBeNull();
  const lease = await soleLease(host, chunkId);
  // The transcript lane tried and was refused, so the hub holds none of the lease's transcript.
  expect((await captured(mockHub, 'POST', '/api/fleet/transcripts')).length).toBeGreaterThanOrEqual(1);
  expect(await hubTurns(mockHub, host, chunkId, lease)).toEqual([]);

  await mockHub.levers.clear('unreachable_transcripts');
  await stepUntil(host, () => hubTurns(mockHub, host, chunkId, lease), (held) => held.length > 0);
  // Still done, not re-driven past it: the recovery flush touched no chunk state.
  expect(await chunkStatus(mockHub, host, chunkId)).toBe('done');
});

test('test_transcript_route_slow_never_blocks_the_fact_lane', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerConfig: { transcripts: { ship: true } } });
  const chunkId = await seed(mockHub);
  // Global, not chunk-scoped: the transcripts route's path names no chunk; each record names its own in the body.
  await mockHub.levers.arm('delay_transcripts', { payload: { ms: 300 } });

  await stepUntil(host, () => chunkStatus(mockHub, host, chunkId), (status) => status === 'done');
  expect((await completionFact(host, chunkId))?.acked_at).not.toBeNull();
  const lease = await soleLease(host, chunkId);
  await stepUntil(host, () => hubTurns(mockHub, host, chunkId, lease), (held) => held.length > 0);
  expect(await chunkStatus(mockHub, host, chunkId)).toBe('done');
});

test('test_stale_envelope_is_tolerated_and_the_chunk_still_lands', async ({ mockHub, runnerHost }) => {
  const chunkId = await seed(mockHub);
  // A stale-epoch envelope on the re-read: the runner fences its completion on its own lease's epoch, not the envelope's.
  await mockHub.levers.arm('stale_envelope', { chunk_id: chunkId, remaining: 1 });

  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');
  expect((await completionFact(runnerHost, chunkId))?.acked_at).not.toBeNull();
});

test('test_a_real_runners_registration_carries_every_declared_subscription', async ({ mockHub, runnerHosts }) => {
  // `probe` names a provider no sampler binds; the host still declares it.
  const host = await runnerHosts.create({ runnerConfig: { subscription: [{ slug: 'probe', name: 'Probe', provider: 'none-such' }] } });

  expect((await host.stepHost()).outcome).toBe('completed');

  // A runner under a host sends no roster of its own: the host's report carries every subscription it declares.
  const reports = await captured(mockHub, 'POST', '/api/fleet/host');
  expect(reports.length).toBeGreaterThanOrEqual(1);
  const declared = (reports.at(-1)!.body as { subscriptions: { slug: string }[] }).subscriptions.map((subscription) => subscription.slug);
  expect(declared.sort()).toEqual(['anthropic', 'probe']);
});

test('test_a_real_runners_registration_carries_the_gates_it_imposes', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerConfig: { runner: [{ name: RUNNER, gates: ['build', 'review'] }] } });

  await steps(host, 1);

  expect((await runnerView(mockHub, host)).gates).toEqual(['build', 'review']);
});

test('test_pull_abandons_the_active_lease_when_the_hub_reports_the_chunk_stopped', async ({ mockHub, runnerHost }) => {
  const chunkId = await seed(mockHub);
  await steps(runnerHost, 1);
  const lease = await soleLease(runnerHost, chunkId);
  expect(lease.closed_at).toBeNull();

  expect((await mockHub.seed.stop(chunkId)).status).toBe(200);

  const closed = await stepUntil(runnerHost, () => soleLease(runnerHost, chunkId), (current) => current.closed_at !== null);
  // Released by PULL's abandon, not transitioned by an ordinary completion racing the stop.
  expect(closed).toMatchObject({ lease_id: lease.lease_id, closure_reason: 'released' });
  expect(await chunkStatus(mockHub, runnerHost, chunkId)).toBe('stopped');
});

test('test_pull_closes_the_local_escalation_when_the_hub_reports_the_chunk_stopped', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  // A crashing judgement with no retry: the runner escalates on the first attempt.
  const chunkId = await seed(mockHub, buildThenDeliver({ judgement_prompt: 'crash()\n', retries_max: 0 }));
  const open = async () => (await runner.escalations()).body.items.filter((item) => item.chunk_id === chunkId);

  const [escalation] = await stepUntil(runnerHost, open, (items) => items.length > 0);
  expect(escalation!.closure).toBeNull();

  expect((await mockHub.seed.stop(chunkId)).status).toBe(200);
  await stepUntil(runnerHost, open, (items) => items.length === 0);

  const closed = (await runner.escalations({ state: 'closed' })).body.items.filter((item) => item.chunk_id === chunkId);
  expect(closed).toHaveLength(1);
  // Closed by PULL's own stopped-chunk sweep, after the lease it escalated had closed.
  expect(closed[0]!.closure?.reason).toBe('stopped');
  expect(Date.parse(closed[0]!.closure!.closed_at)).toBeGreaterThan(Date.parse(closed[0]!.closed_at));
});

// The worker mints matched Edit and Bash tool turns through the mock's own `apply_diff` and `commit`, then pushes its branch.
const TRANSCRIPT_DIFF = [
  'diff --git a/transcript-proof.txt b/transcript-proof.txt',
  'new file mode 100644',
  'index 0000000..1111111',
  '--- /dev/null',
  '+++ b/transcript-proof.txt',
  '@@ -0,0 +1 @@',
  '+minted by the real mock-claude-code transcript writer',
  '',
].join('\n');
const TRANSCRIPT_COMMIT_MESSAGE = 'feat: mint a transcript-provable commit';
const TRANSCRIPT_BUILD = [
  'import pathlib, subprocess',
  'from blizzard_mock.harness.engine import current_context',
  'ctx = current_context()',
  `ctx.cwd = pathlib.Path(ctx.cwd) / '${REPO}'`,
  `subprocess.run(['git', '-C', str(ctx.cwd), 'switch', '-C', '${BRANCH}'], check=True, capture_output=True)`,
  `apply_diff(${JSON.stringify(TRANSCRIPT_DIFF)})`,
  `commit('${TRANSCRIPT_COMMIT_MESSAGE}')`,
  `subprocess.run(['git', '-C', str(ctx.cwd), 'push', 'origin', '${BRANCH}'], check=True, capture_output=True)`,
  '',
].join('\n');
const VERDICT_TEXT = 'the mock harness committed the change; checks are green';
/** Holds the worker's judgement turn, and so its lease, open: the spawn turn has ended and recorded its session. */
const HOLD_OPEN = 'import time\ntime.sleep(120)\n';

/** The chunk a release lever names: none by that id exists, so the lever changes nothing the runner does. */
const RELEASE_CHUNK = 'ch_release_judgement';

/**
 * A judgement that holds its lease open until the test arms a lever on `RELEASE_CHUNK`, read from the mock hub's control
 * plane, then passes.
 */
function judgementReleasedBy(mockHub: MockHub): string {
  return [
    'import json, time, urllib.request',
    'opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))',
    `while not any(lever.get('chunk_id') == '${RELEASE_CHUNK}' for lever in json.load(opener.open('${mockHub.url}/_levers'))['active']):`,
    '    time.sleep(0.1)',
    `verdict('pass', '${VERDICT_TEXT}')`,
    '',
  ].join('\n');
}

/** Steps until the chunk's sole lease is running and the mock harness has recorded its spawn credential, current as the runner last issued it. */
async function runningLeaseWithWorker(host: RunnerHost, chunkId: string) {
  const runner = host.runners[RUNNER]!;
  const lease = await stepUntil(
    host,
    async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'),
    (found) => found !== undefined,
  );
  const headers = await stepUntil(
    host,
    async () => {
      try {
        // The runner re-mints a lease's token for each resume, so the record is read until the credential it holds answers.
        const candidate = host.workerHeaders(lease!.lease_id);
        return (await runner.leaseArtifacts(lease!.lease_id, { scope: 'graph', headers: candidate })).status === 200 ? candidate : undefined;
      } catch {
        return undefined;
      }
    },
    (found) => found !== undefined,
  );
  return { lease: lease!, headers: headers! };
}

/** One transcript turn as the wire carries it, at its defaults. */
function segmentTurn(index: number, kind: string, text: string) {
  return { index, kind, timestamp: null, text, tool: null, thinking_redacted: false, sidechain: null, truncated: false };
}

test('test_transcript_is_read_back_through_the_runner_http_api', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = await seed(mockHub, buildThenDeliver({ prompt: TRANSCRIPT_BUILD, judgement_prompt: `verdict('pass', '${VERDICT_TEXT}')\n` }));
  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');

  const transcript = await runner.leaseTranscript((await soleLease(runnerHost, chunkId)).lease_id);
  expect(transcript.status).toBe(200);
  const body = transcript.body;
  // Available and genuinely parsed, not a stub: a closed lease's session id still reads.
  expect(body.available).toBe(true);
  expect(body.session_id).toBeTruthy();
  const turns = body.turns as { kind: string; text?: string | null; tool?: { name: string; input: unknown; output: string | null } | null }[];
  const kinds = turns.map((turn) => turn.kind);
  expect(kinds).toContain('env');
  expect(kinds).toContain('asst');

  const tools = Object.fromEntries(turns.filter((turn) => turn.kind === 'tool').map((turn) => [turn.tool!.name, turn.tool!]));
  expect(Object.keys(tools)).toEqual(expect.arrayContaining(['Edit', 'Bash']));
  expect(tools.Edit!.output).toBeTruthy();
  expect(tools.Bash!.output).toBeTruthy();
  // The structured input survives the wire as a mapping, never a re-materialized JSON string.
  expect(typeof tools.Bash!.input).toBe('object');

  // Provenance: the commit the origin holds appears in the Bash turn's output — content only the real commit created.
  const sha = await runnerHost.originHead(REPO, BRANCH);
  expect(sha).toMatch(/^[0-9a-f]{40}$/);
  expect(tools.Bash!.output).toContain(sha!.slice(0, 7));
  expect(tools.Bash!.output).toContain(TRANSCRIPT_COMMIT_MESSAGE);

  const verdict = turns.find((turn) => turn.kind === 'asst')!;
  expect(verdict.text).toContain('pass');
  expect(verdict.text).toContain(VERDICT_TEXT);
});

test('test_graph_scoped_artifact_reads_from_the_runners_own_pin_with_the_hub_unreachable', async ({ mockHub, runnerHost }) => {
  // The node read below outwaits the runner's bounded hub retry before its 503 surfaces.
  test.slow();
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = await seed(mockHub, {
    ...buildThenDeliver({ judgement_prompt: HOLD_OPEN }),
    graph_artifacts: [{ name: 'docket', kind: 'asset', content: 'the docket text' }],
  });
  const { lease, headers } = await runningLeaseWithWorker(runnerHost, chunkId);

  // No step runs past here, so the lease stays open for every read below.
  await mockHub.levers.arm('unreachable', { remaining: 10_000 });

  const listed = await runner.leaseArtifacts(lease.lease_id, { scope: 'graph', headers });
  expect(listed.status).toBe(200);
  expect(listed.body.map((artifact) => artifact.name)).toEqual(['docket']);
  expect(listed.body[0]).toMatchObject({ scope: 'graph', content: 'the docket text' });

  const fetched = await runner.raw.get<{ content: string }>(`/api/leases/${lease.lease_id}/artifacts/docket`, { params: { scope: 'graph' }, headers });
  expect(fetched.status).toBe(200);
  expect(fetched.body.content).toBe('the docket text');

  // Contrast: the node half still proxies to the hub, so the same outage refuses it, the lever's 503 passed through.
  const nodeRead = await runner.expecting(503).leaseArtifacts(lease.lease_id, { scope: 'node', headers });
  expect(nodeRead.status).toBe(503);
  expect((nodeRead.body as unknown as { detail: string }).detail).toContain('unreachable');
});

test('test_a_closed_leases_transcript_resolves_to_the_hub_through_the_runner_api', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const chunkId = await seed(mockHub, buildThenDeliver({ prompt: TRANSCRIPT_BUILD, judgement_prompt: judgementReleasedBy(mockHub) }));

  // Open: the lease's own transcript, read locally.
  const openRead = await stepUntil(
    runnerHost,
    async () => {
      const lease = (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running');
      return lease === undefined ? undefined : (await runner.leaseTranscript(lease.lease_id)).body;
    },
    (read) => read !== undefined && read.available && read.turns.length > 0,
  );
  expect(openRead).toMatchObject({ provenance: 'local', hub_unreachable: false });

  await mockHub.levers.arm('stale_envelope', { chunk_id: RELEASE_CHUNK });
  await stepUntil(runnerHost, () => chunkStatus(mockHub, runnerHost, chunkId), (status) => status === 'done');
  const lease = await soleLease(runnerHost, chunkId);
  const read = async () => {
    const answer = await runner.leaseTranscript(lease.lease_id);
    expect(answer.status).toBe(200);
    return answer.body;
  };

  // Closed, the hub reachable but holding nothing: a definite answer, so local.
  expect(await read()).toMatchObject({ provenance: 'local', available: true });

  // A segment well past the runner's own lane, so the mock hub retains it rather than reading it as already held.
  const seq = 10_000;
  const shipped = await mockHub.fleetOf(runnerHost, RUNNER).post<{ applied: number[] }>('/api/fleet/transcripts', {
    data: {
      records: [
        {
          seq,
          segment_id: 'sg_1',
          chunk_id: chunkId,
          node_id: lease.node_id,
          epoch: lease.epoch,
          spawn_generation: 1,
          turn_range_start: 0,
          turn_range_end: 1,
          final: true,
          normalizer_version: 'v1',
          harness_version: 'claude-code-1.0',
          turns: [segmentTurn(0, 'asst', 'from the hub archive'), segmentTurn(1, 'thinking', 'and its reasoning')],
        },
      ],
    },
  });
  expect(shipped.status).toBe(200);
  expect(shipped.body.applied).toEqual([seq]);

  const archived = await read();
  expect(archived).toMatchObject({ provenance: 'archived', hub_unreachable: false });
  // A thinking turn reaches the panel alongside the assistant turn.
  expect(archived.turns.map((turn) => [turn.kind, turn.text])).toEqual([
    ['asst', 'from the hub archive'],
    ['thinking', 'and its reasoning'],
  ]);

  // Rotated away locally, the panel is undegraded: the hub still answers.
  runnerHost.rotateTranscripts();
  const rotated = await read();
  expect(rotated.provenance).toBe('archived');
  expect(rotated.turns.map((turn) => turn.text)).toEqual(['from the hub archive', 'and its reasoning']);

  // The hub gone: the flag, not `reason`, is what the panel branches on.
  await mockHub.stop();
  expect(await read()).toMatchObject({ hub_unreachable: true, provenance: 'local' });
  expect((await runner.leaseTranscript('no-such-lease')).status).toBe(404);

  // A recorded owner the host no longer binds is a refusal naming it, never a read through another harness.
  await runnerHost.stop();
  runnerHost.reconfigure({ runner: [{ name: RUNNER, claude_code: { enabled: false }, opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] });
  await runnerHost.restart();
  const unknownOwner = await runner.expecting(503).leaseTranscript(lease.lease_id);
  expect(unknownOwner.status).toBe(503);
  expect((unknownOwner.body as unknown as { detail: string }).detail).toContain("unknown coding harness 'claude_code'");
});

test('test_runner_stream_delivers_live_and_replays_from_last_event_id', async ({ mockHub, runnerHost, runnerStream }) => {
  const live = await runnerStream(runnerHost.url, { fromNow: true });
  expect(live.contentType).toMatch(/^text\/event-stream/);
  await seed(mockHub);
  const leaseFrames = () => live.frames.filter((frame) => frame.event === 'lease-changed');
  await stepUntil(runnerHost, async () => leaseFrames().length, (count) => count >= 2);
  const [first, second] = leaseFrames();
  expect(first!.data).toMatchObject({ cause: 'created' });

  // A reconnect naming the first frame's id replays only what followed it: the second, never the first again.
  const replay = await runnerStream(runnerHost.url, { headers: { 'Last-Event-ID': String(first!.id) }, reconnect: false });
  await replay.waitFor((frame) => frame.id === second!.id);
  const replayed = replay.frames.filter((frame) => frame.id <= second!.id);
  expect(replayed.map((frame) => frame.id)).not.toContain(first!.id);
  expect(replayed).toEqual(live.frames.filter((frame) => frame.id > first!.id && frame.id <= second!.id));
  expect(replayed.filter((frame) => frame.event === 'lease-changed')).toEqual([second]);
});

test.describe('a host under a hub offering an IdP', () => {
  // The runner probes the hub's auth mode once per process, so the IdP is on before the host's first boot.
  test.use({ mockHubOfferIdp: true, runnerStarted: false });

  test('test_events_stream_401s_without_a_session_over_tcp_under_oauth', async ({ runnerHost }) => {
    await runnerHost.restart();
    const stream = await runnerHost.api.raw.get('/api/events/stream');
    expect(stream.status).toBe(401);
  });
});

test('test_runner_sigterm_returns_promptly_with_a_client_parked_on_the_stream', async ({ runnerHosts, runnerStream }) => {
  const host = await runnerHosts.create({ runnerLoop: 'timed', runnerEnv: { BZ_RUNNER_TICK_SECONDS: '1' } });
  // A pass has ended, so shutdown joins no startup pass: the stream's drain is the subject.
  await expect.poll(async () => (await host.runners[RUNNER]!.status()).body.last_tick_at, { timeout: 30_000 }).not.toBeNull();
  const parked = await runnerStream(host.url, { reconnect: false });

  const started = Date.now();
  const exit = await host.stop();
  // Inside uvicorn's own 5s graceful-drain bound, and an exit rather than the fixture's kill.
  expect(Date.now() - started).toBeLessThan(2_000);
  expect(typeof exit).toBe('number');
  await parked.waitForEnd();
});

test('test_a_hung_trace_export_never_delays_a_tick_or_holds_shutdown', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerLoop: 'timed',
    runnerOtlp: 'hung',
    runnerEnv: { BZ_RUNNER_TICK_SECONDS: '0.5', OTEL_EXPORTER_OTLP_TRACES_TIMEOUT: '120' },
    runnerConfig: { tracing: { sweep_seconds: 1n, settle_seconds: 0n } },
  });
  // A chunk run to done on the timed loop, then the pass clock watched across several passes.
  test.slow();
  const runner = host.runners[RUNNER]!;
  // A closed lease is what the sweep tells: one chunk run to done.
  const chunkId = await seed(mockHub);
  await expect.poll(() => chunkStatus(mockHub, host, chunkId), { timeout: 120_000 }).toBe('done');
  await expect.poll(() => host.otlp!.openConnections(), { timeout: 15_000 }).toBeGreaterThan(0);

  // With the export hung, passes keep ending: the pass clock advances three times over.
  const tickAt = async () => (await runner.status()).body.last_tick_at;
  let previous = await tickAt();
  for (let advance = 0; advance < 3; advance++) {
    await expect.poll(tickAt, { timeout: 10_000 }).not.toBe(previous);
    previous = await tickAt();
  }

  const started = Date.now();
  const exit = await host.stop();
  expect(Date.now() - started).toBeLessThan(20_000);
  expect(typeof exit).toBe('number');
  expect(host.log()).toContain('lease trace sweep still running at shutdown');
});

/** The distinct seqs of the `trace-config-rejected` events the mock hub received in fact pushes. */
async function traceConfigRejections(mockHub: MockHub): Promise<number[]> {
  const pushes = await captured(mockHub, 'POST', '/api/fleet/events');
  const facts = pushes.flatMap((request) => ((request.body as { facts?: { seq: number; payload: { kind?: string } }[] } | null)?.facts ?? []));
  return [...new Set(facts.filter((fact) => fact.payload.kind === 'trace-config-rejected').map((fact) => fact.seq))];
}

test('test_a_grpc_protocol_serves_with_tracing_off_and_one_rejection_reaches_the_hub', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerStarted: false,
    runnerEnv: { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://127.0.0.1:4318', OTEL_EXPORTER_OTLP_PROTOCOL: 'grpc' },
    runnerConfig: { tracing: { sweep_seconds: 1n, settle_seconds: 0n } },
  });
  const runner = host.runners[RUNNER]!;
  // Held unreachable from the boot: the rejection is buffered under the runner and waits there, unsent.
  await mockHub.levers.arm('unreachable', { remaining: 10_000 });
  await host.restart();
  await steps(host, 2);
  expect(await runner.bufferDepth()).toBeGreaterThanOrEqual(1);
  expect(await traceConfigRejections(mockHub)).toEqual([]);

  await mockHub.levers.reset();
  await stepUntil(host, () => runner.bufferDepth(), (depth) => depth === 0);
  // One rejection reached the hub; the host still serves with tracing off.
  expect(await traceConfigRejections(mockHub)).toHaveLength(1);
  expect((await host.api.raw.get('/api/health')).status).toBe(200);
});
