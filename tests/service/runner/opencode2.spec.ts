import { expect, leaseProgress, runnerTest as test, stepUntil } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// The OpenCode 2 binding on a stepped runner host: the configured binary is the engine-backed `mock-opencode-2`, whose
// `--version` makes the runner compose OpenCode 2. No service runs when the host boots; the runner starts it before
// its first launch, and every turn is a `blizzard runner opencode drive` against it, its prompt a fenced script.

const STALL_PASSES = 600;
const POOL = 'opencode2-pool';

test.setTimeout(120_000);

const OPENCODE2 = { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockOpencode2}' } }] };
test.use({ runnerConfig: OPENCODE2 });

/** A prompt the engine runs: the runner composes prose around every turn's message, so the script is tagged. */
function script(...lines: string[]): string {
  return ['<behavior-script>', ...lines, '</behavior-script>', ''].join('\n');
}

const BUILD = script("built = shell('echo built-by-opencode-2 > BUILT.md && cat BUILT.md')", "text('BUILT ' + built.output.strip())");
const JUDGE_PASS = script("text('<Choice>pass</Choice> the turn ran and its file holds')");

/** A build then review chunk under OpenCode 2, the two nodes sharing one session pool. */
const buildAndReview: MockChunkSpec = {
  graph_id: 'gr_service_opencode2',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      judged_by: 'worker',
      prompt: BUILD,
      judgement_prompt: JUDGE_PASS,
      choices: [{ name: 'pass', description: 'built', to: 'review' }],
      retries_max: 1,
    },
    review: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      judged_by: 'worker',
      prompt: script("text('REVIEWED')"),
      judgement_prompt: JUDGE_PASS,
      choices: [{ name: 'pass', description: 'reviewed', to: 'done' }],
      retries_max: 1,
    },
  },
};

/** A one-node chunk whose build runs ``prompt``. */
function single(graphId: string, prompt: string, retries = 1): MockChunkSpec {
  return {
    graph_id: graphId,
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        session_harnesses: ['opencode'],
        judged_by: 'worker',
        prompt,
        judgement_prompt: JUDGE_PASS,
        choices: [{ name: 'pass', description: 'built', to: 'done' }],
        retries_max: retries,
      },
    },
  };
}

/** Seeds `spec` on `host`, and returns what a case reads its chunk through. */
async function seeded(mockHub: MockHub, host: RunnerHost, spec: MockChunkSpec) {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const chunkId = (await mockHub.seed.chunk(spec)).body.chunk_id;
  const runner = host.runners['runner-local']!;
  const status = async (): Promise<string> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.status as string;
  const leases = async () => (await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId).sort((a, b) => a.epoch - b.epoch);
  const until = async (wanted: (status: string) => boolean): Promise<void> => {
    try {
      await stepUntil(host, status, wanted, { stallPasses: STALL_PASSES });
    } catch (error) {
      const held = await leases();
      const turns = await Promise.all(held.map(async (lease) => (await runner.leaseTranscript(lease.lease_id)).body.turns.map((turn) => `${turn.kind}: ${turn.text}${turn.tool ? ` ${turn.tool.name} ${turn.tool.output}` : ''}`)));
      throw new Error(`${String(error)}\nleases: ${JSON.stringify(held.map((lease) => [lease.epoch, lease.state, lease.closure_reason]))}\ntranscripts: ${JSON.stringify(turns)}`, { cause: error });
    }
  };
  return { chunkId, runner, status, leases, landed: () => until((current) => current === 'done'), until };
}

test('test_opencode2_build_and_review_resume_one_service_session_to_done', async ({ mockHub, runnerHost }) => {
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, buildAndReview);
  const health = (await runner.harnessHealth()).body.items.find((item) => item.harness_id === 'opencode');
  expect(health?.available, JSON.stringify(health)).toBe(true);

  await landed();

  const held = await leases();
  expect(held.map((lease) => lease.node_name)).toEqual(['build', 'review']);
  expect(held[0]!.session_id).toMatch(/^ses_/);
  expect(held[1]!.session_id, 'review resumed the session build minted').toBe(held[0]!.session_id);
  // The version recorded on every spawn is the service's own, as `GET /api/info` reports it.
  expect(held.map((lease) => lease.harness_version)).toEqual(['2.1.4', '2.1.4']);
});

test('test_opencode2_transcripts_pump_the_services_turns_with_their_tool_calls', async ({ mockHub, runnerHost }) => {
  const { chunkId, runner, leases, landed } = await seeded(mockHub, runnerHost, single('gr_opencode2_transcript', BUILD));
  await landed();

  const lease = (await leases())[0]!;
  const body = (await runner.leaseTranscript(lease.lease_id)).body;
  expect(body.available).toBe(true);
  const shell = body.turns.find((turn) => turn.kind === 'tool' && turn.tool?.name === 'shell');
  expect(shell?.tool?.output).toContain('built-by-opencode-2');
  expect(body.turns.some((turn) => turn.kind === 'asst' && turn.text.includes('BUILT built-by-opencode-2'))).toBe(true);
  const verdict = [...body.turns].reverse().find((turn) => turn.kind === 'asst');
  expect(verdict?.text).toContain('<Choice>pass</Choice>');
  const segments = (await runner.transcriptSegments(chunkId)).body.segments;
  expect(segments.length).toBeGreaterThan(0);
  expect(segments.every((segment) => segment.harness_id === 'opencode')).toBe(true);
});

test('test_opencode2_an_answered_ask_nudges_the_dormant_session', async ({ mockHub, runnerHost }) => {
  const ask = script(`shell(${JSON.stringify(`${runnerHost.cliBinary} runner ask 'Which style?' --options 'rest|graphql'`)})`, "text('ASKED')");
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, single('gr_opencode2_ask', ask));
  const asked = await stepUntil(runnerHost, async () => (await runner.asks()).body.items, (items) => items.length > 0, { stallPasses: STALL_PASSES });
  // The asking turn ends, its usage lands, and the chunk parks on its question, all in one pass.
  const parked = (await leases())[0]!;
  await stepUntil(runnerHost, async () => (await runner.leaseUsage(parked.lease_id)).body.items, (facts) => facts.some((fact) => fact.kind === 'spawn'), { stallPasses: STALL_PASSES });

  // The question reaches the hub when the runner's outbound lane next flushes it.
  const answer = { question_id: asked[0]!.question_id, answer: script("text('ANSWERED rest')") };
  await stepUntil(runnerHost, async () => (await mockHub.seed.answer(answer)).status, (status) => status === 200, { stallPasses: STALL_PASSES });
  await landed();

  const lease = (await leases())[0]!;
  const turns = (await runner.leaseTranscript(lease.lease_id)).body.turns;
  expect(turns.some((turn) => turn.kind === 'asst' && turn.text.includes('ANSWERED rest')), 'the nudge ran in the asking session').toBe(true);
});

test('test_opencode2_takeover_hands_the_session_to_attend_and_stop_closes_the_lease', async ({ mockHub, runnerHost }) => {
  const { chunkId, runner, leases } = await seeded(mockHub, runnerHost, single('gr_opencode2_takeover', script("shell('sleep 60')")));
  const live = await stepUntil(runnerHost, leases, (held) => held.some((lease) => lease.session_id !== null && lease.state !== 'closed'), { stallPasses: STALL_PASSES, progress: leaseProgress });
  const sessionId = live[0]!.session_id!;

  const opened = await runner.openTakeover(chunkId, { force: true });
  expect(opened.status, JSON.stringify(opened.body)).toBe(201);
  expect(JSON.stringify(opened.body)).toContain(`runner opencode attend ${sessionId}`);

  expect((await mockHub.seed.stop(chunkId)).status).toBe(200);
  await stepUntil(runnerHost, leases, (held) => held.every((lease) => lease.state === 'closed'), { stallPasses: STALL_PASSES, progress: leaseProgress });
});

test.describe('with a service that never answers', () => {
  test('test_opencode2_an_unreachable_service_fails_the_turn_as_a_spawn_failure', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['NEVER_REGISTERED'] });
    const { runner, leases } = await seeded(mockHub, host, single('gr_opencode2_unreachable', BUILD, 0));
    // The launch's spawn failure fails the pass it happens in; the next pass reaps the lease it left.
    const outcomes: string[] = [];
    let held = await leases();
    while (!held.some((lease) => lease.state === 'closed') && outcomes.length < 4) {
      outcomes.push((await host.step()).outcome);
      held = await leases();
    }

    expect(outcomes[0], 'the pass that tried the launch').toBe('failed');
    expect(held.map((lease) => lease.state)).toEqual(['closed']);
    expect(held[0]!.session_id, 'no session was minted on a service that never answered').toBeNull();
    expect(host.logTail()).toContain('no OpenCode service to drive the turn on');
    const facts = (await runner.facts({ limit: 200 })).body.items;
    expect(facts.map((fact) => fact.kind), 'with no retry left, the chunk escalates').toContain('escalation.recorded');
  });
});
