import { expect, runnerTest as test } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// The OpenCode binding on a stepped runner host: worker turns run under `mock-opencode`, every wait is a step of the host,
// and each case reads the lease, usage, transcript and segment routes of the runner and the mock's own world records.

const REPO = 'toy-api';
const BUILD_BRANCH = 'feature/scripted-build';
const WORK_REFS = [{ source: 'mock', ref: 'blizzard/toy-api/issues/1' }];
const MAX_STEPS = 60;

test.use({
  runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] },
});

const SWITCH_BRANCH = [
  'import os, pathlib, subprocess',
  `repo_path = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${REPO}')`,
  `subprocess.run(['git', '-C', repo_path, 'switch', '-C', '${BUILD_BRANCH}'], check=True, capture_output=True)`,
];

// Every `subprocess.run` captures its output: mock-opencode reads the first stdout line as the fresh session's identity.
const PUSH_AND_DECLARE = [
  "_branch = subprocess.run(['git', '-C', repo_path, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "_commit = subprocess.run(['git', '-C', repo_path, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "subprocess.run(['git', '-C', repo_path, 'push', 'origin', _branch], check=True, capture_output=True)",
  `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', '${REPO}', '--branch', _branch, '--commit', _commit], capture_output=True)`,
];

const OPENCODE_BUILD = [
  ...SWITCH_BRANCH,
  "(pathlib.Path(repo_path) / 'LANDED.md').write_text('landed by the mock harness\\n')",
  "subprocess.run(['git', '-C', repo_path, 'add', '-A'], check=True, capture_output=True)",
  "subprocess.run(['git', '-C', repo_path, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change from the mock harness'], check=True, capture_output=True)",
  ...PUSH_AND_DECLARE,
  '',
].join('\n');

const JUDGEMENT = "verdict('pass', 'the mock harness committed the change; checks are green')\n";
const POOL = 'opencode-pool';

const DELIVER = {
  executor: 'hub',
  judgement: {
    choices: {
      landed: { description: 'Every repo merged cleanly.', to: 'done' },
      conflict: { description: 'A repo did not merge cleanly.', to: 'build' },
      failure: { description: 'A repo failed to land.', to: 'build' },
    },
  },
};

/** A build then review chunk entirely under OpenCode, the two nodes sharing one session pool. */
const buildAndReview: MockChunkSpec = {
  graph_id: 'gr_service_opencode',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      judged_by: 'worker',
      prompt: OPENCODE_BUILD,
      judgement_prompt: JUDGEMENT,
      choices: [{ name: 'pass', description: 'committed and green', to: 'review' }],
      retries_max: 1,
    },
    review: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      judged_by: 'worker',
      prompt: 'pass\n',
      judgement_prompt: "verdict('pass', 'resumed the same OpenCode session; review complete')\n",
      choices: [{ name: 'pass', description: 'cold-eyes review: ready to land', to: 'done' }],
      retries_max: 1,
    },
  },
  work_refs: WORK_REFS,
};

/** A build then deliver chunk around one build program and its judgement script. */
function buildThenDeliver(graphId: string, prompt: string, judgement: string): MockChunkSpec {
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
        judgement_prompt: judgement,
        choices: [{ name: 'pass', description: 'committed and green', to: 'deliver' }],
        retries_max: 1,
      },
      deliver: DELIVER,
    },
    work_refs: WORK_REFS,
  };
}

const TRANSCRIPT_DIFF = [
  'diff --git a/transcript-proof.txt b/transcript-proof.txt',
  'new file mode 100644',
  'index 0000000..1111111',
  '--- /dev/null',
  '+++ b/transcript-proof.txt',
  '@@ -0,0 +1 @@',
  '+minted by the real mock-opencode transcript writer',
  '',
].join('\n');
const COMMIT_MESSAGE = 'feat: mint a transcript-provable commit under OpenCode';

// The mock's own `apply_diff` and `commit` helpers mint matched `Edit` and `Bash` tool turns.
const TRANSCRIPT_BUILD = [
  'import pathlib, subprocess',
  'from blizzard_mock.harness.engine import current_context',
  'ctx = current_context()',
  `ctx.cwd = pathlib.Path(ctx.cwd) / '${REPO}'`,
  `subprocess.run(['git', '-C', str(ctx.cwd), 'switch', '-C', '${BUILD_BRANCH}'], check=True, capture_output=True)`,
  `apply_diff(${JSON.stringify(TRANSCRIPT_DIFF)})`,
  `commit(${JSON.stringify(COMMIT_MESSAGE)})`,
  'repo_path = str(ctx.cwd)',
  ...PUSH_AND_DECLARE,
  '',
].join('\n');

const transcriptChunk = buildThenDeliver('gr_opencode_transcript', TRANSCRIPT_BUILD, JUDGEMENT);

const CRASH_MARKER = 'crash-recovered-once.marker';
const DONE_MARKER = 'build-done.marker';

/** The build program crashes the first lease hard after `beforeCrash`; the retry finds the marker and does the real work. */
function crashBuild(beforeCrash: string[]): string {
  return [
    'import pathlib, subprocess',
    `marker = pathlib.Path('${CRASH_MARKER}')`,
    'if not marker.exists():',
    ...beforeCrash.map((line) => `    ${line}`),
    "    marker.write_text('crashed once\\n')",
    '    crash(hard=True)',
    ...TRANSCRIPT_BUILD.split('\n').slice(1),
    `pathlib.Path('${DONE_MARKER}').write_text('done\\n')`,
    '',
  ].join('\n');
}

const CRASH_JUDGEMENT = [
  'import pathlib',
  `if pathlib.Path('${DONE_MARKER}').exists():`,
  "    verdict('pass', 'the mock harness committed the change; checks are green')",
  '',
].join('\n');

const crashChunk = (beforeCrash: string[]) => buildThenDeliver('gr_opencode_crash_recovery', crashBuild(beforeCrash), CRASH_JUDGEMENT);

const CRASH_AFTER_EDIT = ["tool_call('Edit', {'path': 'partial.txt'}, output='edited just before the crash')"];
const TASK_CALL = "tool_call('task', {'subagent_type': 'explorer', 'prompt': 'look around'}, output='delegated')";
// The root's own step-finish, printed by the script itself, so the test knows its input of 7.
const ROOT_STEP_BEFORE_TASK = [
  'import json',
  'from blizzard_mock.harness.engine import current_context',
  'sid = current_context().session.session_id',
  "print(json.dumps({'type': 'step_finish', 'sessionID': sid, 'part': {'id': 'prt_before_task', 'sessionID': sid, 'messageID': 'msg_before_task', 'type': 'step-finish', 'reason': 'stop', 'cost': 0, 'tokens': {'input': 7, 'output': 1, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}}}), flush=True)",
  TASK_CALL,
];

/** Steps the host until `reached` holds, failing after `MAX_STEPS` passes with the last state it saw. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    await host.step();
    state = await read();
  }
  return state;
}

/** The sum of the input tokens of the `step-finish` parts of `messages`. */
function stepInput(messages: { parts: { type: string; tokens?: { input: number } }[] }[]): number {
  return messages.flatMap((message) => message.parts).reduce((sum, part) => sum + (part.type === 'step-finish' ? (part.tokens?.input ?? 0) : 0), 0);
}

function stepCost(messages: { parts: { type: string; cost?: number }[] }[]): number {
  return messages.flatMap((message) => message.parts).reduce((sum, part) => sum + (part.type === 'step-finish' ? (part.cost ?? 0) : 0), 0);
}

/** Seeds `spec`, and returns what a case reads its chunk through. */
async function seeded(mockHub: MockHub, runnerHost: RunnerHost, spec: MockChunkSpec) {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const chunkId = (await mockHub.seed.chunk(spec)).body.chunk_id;
  const runner = runnerHost.runners['runner-local']!;
  const status = async (): Promise<string> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.status as string;
  const leases = async () => (await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId).sort((a, b) => a.epoch - b.epoch);
  const landed = async (): Promise<void> => {
    await stepUntil(runnerHost, status, (current) => current === 'done');
  };
  return { chunkId, runner, status, leases, landed };
}

test('test_opencode_build_and_review_resume_the_same_session_to_done', async ({ mockHub, runnerHost }) => {
  const { leases, landed } = await seeded(mockHub, runnerHost, buildAndReview);
  await landed();

  const held = await leases();
  expect(held.map((lease) => lease.node_name)).toEqual(['build', 'review']);
  expect(held[0]!.session_id).toBeTruthy();
  expect(held[1]!.session_id).toBe(held[0]!.session_id);
  // One session was ever minted: build and review shared it through the pool, never forking a second.
  const sessions = await runnerHost.mockSessions();
  expect(sessions).toHaveLength(1);
  // Build's fresh mint, its judgement resume, review's node-entry resume, and its judgement resume.
  expect(sessions[0]!.invocations.map((invocation) => invocation.kind)).toEqual(['spawn', 'resume', 'resume', 'resume']);
});

test.describe('with a published bundle', () => {
  test.use({
    runnerBundle: { 'opencode/opencode.json': '{"permission":{"bash":"deny"}}' },
    runnerConfig: {
      runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' }, harness: { config_dir: '${bundle}' } }],
    },
  });

  // The worker reports the permission map of the composed config OpenCode would read, in a tool turn.
  const REPORT_CONFIG = [
    'import json, os',
    "composed = json.load(open(os.environ['OPENCODE_CONFIG']))",
    "tool_call('Bash', {'command': 'config'}, output=json.dumps(composed['permission'], sort_keys=True))",
    '',
  ].join('\n');

  test('test_runner_serves_a_mock_opencode_lease_with_a_published_bundle', async ({ mockHub, runnerHost }) => {
    const { chunkId, runner, leases } = await seeded(mockHub, runnerHost, buildThenDeliver('gr_opencode_bundle', REPORT_CONFIG, JUDGEMENT));
    const dashboard = await runner.dashboard();
    expect(dashboard.body.harness_health.items.find((item) => item.harness_id === 'opencode')?.available).toBe(true);

    const listed = await stepUntil(runnerHost, leases, (held) => held.length > 0);
    const lease = listed[0]!;
    expect((await runner.heartbeat(lease.lease_id)).status).toBe(200);
    // The lease is open until a pass reaps it, so its heartbeat is read before the next step.
    const open = (await leases()).find((held) => held.lease_id === lease.lease_id);
    expect(open?.last_heartbeat_at).not.toBeNull();
    await runnerHost.step();
    expect((await runner.dashboard()).body.runner.last_tick_at).not.toBeNull();

    await stepUntil(runnerHost, leases, (held) => held.some((l) => l.chunk_id === chunkId && l.state === 'closed'));
    const transcript = (await runner.leaseTranscript(lease.lease_id)).body;
    const config = transcript.turns.find((turn) => turn.kind === 'tool' && turn.tool?.name === 'Bash');
    expect(JSON.parse(config?.tool?.output ?? 'null')).toEqual({ bash: 'deny', question: 'deny' });
  });
});

test('test_opencode_transcript_is_read_back_through_the_runner_http_api', async ({ mockHub, runnerHost }) => {
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, transcriptChunk);
  await landed();

  const held = await leases();
  expect(held).toHaveLength(1);
  const body = (await runner.leaseTranscript(held[0]!.lease_id)).body;
  expect(body.available).toBe(true);
  expect(body.session_id, 'a closed lease keeps its session id readable').toBeTruthy();
  const kinds = body.turns.map((turn) => turn.kind);
  expect(kinds).toContain('env');
  expect(kinds).toContain('asst');
  const tools = Object.fromEntries(body.turns.flatMap((turn) => (turn.kind === 'tool' && turn.tool ? [[turn.tool.name, turn.tool]] : [])));
  expect(tools.Edit?.output).toBeTruthy();
  expect(tools.Bash?.output).toBeTruthy();
  expect(typeof tools.Bash?.input).toBe('object');
  const head = await runnerHost.originHead(REPO, BUILD_BRANCH);
  expect(head, 'the mock harness’s commit reached the origin').toBeDefined();
  expect(tools.Bash?.output).toContain(head!.slice(0, 7));
  expect(tools.Bash?.output).toContain(COMMIT_MESSAGE);
  // The build script never asks or gives a verdict itself, so the judgement's verdict is the last assistant turn.
  const verdict = [...body.turns].reverse().find((turn) => turn.kind === 'asst');
  expect(verdict?.text).toContain('pass');
  expect(verdict?.text).toContain('the mock harness committed the change; checks are green');
});

test('test_opencode_transcript_segment_carries_opencode_provenance_through_the_runners_local_index', async ({ mockHub, runnerHost }) => {
  const { chunkId, runner, landed } = await seeded(mockHub, runnerHost, transcriptChunk);
  await landed();

  const segments = (await runner.transcriptSegments(chunkId)).body.segments;
  expect(segments.length).toBeGreaterThan(0);
  expect(segments.every((entry) => entry.harness_id === 'opencode')).toBe(true);
});

test('test_opencode_crash_mid_turn_recovers_with_usage_recorded_exactly_once', async ({ mockHub, runnerHost }) => {
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, crashChunk(CRASH_AFTER_EDIT));
  await landed();

  expect(await runnerHost.originHead(REPO, BUILD_BRANCH), 'the retried build’s commit reached the origin').toBeDefined();
  const held = await leases();
  expect(held, 'the crashed lease and exactly one requeued retry').toHaveLength(2);
  const [crashed, retried] = await Promise.all(held.map(async (lease) => (await runner.leaseUsage(lease.lease_id)).body.items));
  for (const facts of [crashed!, retried!]) {
    const keys = facts.map((fact) => `${fact.generation}/${fact.kind}`);
    expect(new Set(keys).size, `usage recorded twice for one generation and kind: ${keys}`).toBe(keys.length);
  }
  // The crashed worker invocation earns no fact, never a fabricated partial sum; its judgement resume ran, so it earns one.
  expect(crashed!.map((fact) => fact.kind)).toEqual(['judge']);
  expect(crashed![0]!.cost_usd).not.toBeNull();
  // The retry's worker exited clean with a real envelope, so both its spawn and its judge land genuine facts.
  expect(retried!.map((fact) => fact.kind).sort()).toEqual(['judge', 'spawn']);
  expect(retried!.every((fact) => fact.cost_usd !== null)).toBe(true);
});

test('test_opencode_crash_mid_task_records_child_without_a_stdout_tool_use', async ({ mockHub, runnerHost }) => {
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, crashChunk(ROOT_STEP_BEFORE_TASK));
  await landed();

  const crashed = (await leases())[0]!;
  const exports = await runnerHost.opencodeExports();
  const [child] = exports.filter((doc) => doc.info.parentID);
  expect(child, 'the delegated child session was exported').toBeDefined();
  expect(child!.info.parentID).toBe(crashed.session_id);
  const turns = (await runner.leaseTranscript(crashed.lease_id)).body.turns;
  expect(turns.some((turn) => turn.kind === 'tool' && turn.tool?.name === 'task')).toBe(true);
  const facts = (await runner.leaseUsage(crashed.lease_id)).body.items;
  const worker = facts.find((fact) => fact.kind === 'spawn');
  expect(worker).toBeDefined();
  expect(worker!.input_tokens).toBe(7 + stepInput(child!.messages));
  expect(worker!.cost_usd).toBeCloseTo(stepCost(child!.messages), 9);
});

test('test_opencode_spawn_usage_includes_the_task_childs_steps', async ({ mockHub, runnerHost }) => {
  const spec = buildThenDeliver('gr_service_opencode_task', `${TASK_CALL}\n`, JUDGEMENT);
  const { runner, leases, landed } = await seeded(mockHub, runnerHost, spec);
  await landed();

  const exports = await runnerHost.opencodeExports();
  const children = exports.filter((doc) => doc.info.parentID);
  const roots = exports.filter((doc) => !doc.info.parentID);
  expect(children).toHaveLength(1);
  expect(roots).toHaveLength(1);
  // The judgement resume appends to the root document, so the spawn turn ends where the second user message begins.
  const messages = roots[0]!.messages;
  const resumed = messages.findIndex((message, index) => index > 0 && message.info.role === 'user');
  const spawnMessages = resumed === -1 ? messages : messages.slice(0, resumed);
  const facts = (await runner.leaseUsage((await leases())[0]!.lease_id)).body.items;
  const spawn = facts.find((fact) => fact.kind === 'spawn');
  expect(spawn?.input_tokens).toBe(stepInput(spawnMessages) + stepInput(children[0]!.messages));
});
