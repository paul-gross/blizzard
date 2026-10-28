import { expect, type MockChunkSpec, type MockHub, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A real runner host stepped against the test's own mock hub, whose levers manufacture the rare states a runner must
// survive: an unreachable hub, a dropped ack, a stale envelope, a denied claim, a failing or slow transcript route, a
// usage limit, and a chunk stopped under a lease. Every wait is a step of the host; every read is a public surface.

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
