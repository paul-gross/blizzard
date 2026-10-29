import { expect, stepUntil, type MockChunkSpec, runnerTest as test } from '../../harness/fixtures';

// Claude Code bundle delivery on a stepped runner host: a bundle carrying an operator hook drives the mock Claude Code
// worker, which executes the composed `--settings` hooks. The operator's marker hook firing proves the composed file was
// delivered; the stub `blizzard` on the worker's PATH recording a heartbeat proves composition kept the runner's own
// wiring beside it.

test.setTimeout(90_000);

const RUNNER = 'runner-local';

const OPERATOR_HOOK = { type: 'command', command: "sh -c 'echo fired >> ${logs}/operator-hook.log'" };

// The stub `blizzard` the worker's PATH leads with records the runner's own heartbeat hook.
test.use({
  runnerStubs: { blizzard: '#!/bin/sh\necho "$@" >> ${logs}/heartbeats.log\n' },
  runnerBundle: {
    'claude-code/settings.json': JSON.stringify({ hooks: { PostToolUse: [{ hooks: [OPERATOR_HOOK] }] } }),
    'claude-code/mcp.json': '{}',
  },
  runnerConfig: {
    runner: [{ name: RUNNER, harness: { config_dir: '${bundle}' }, worker: { path_prepend: ['${stubs}'] } }],
  },
});

// The scripted build makes a tool call of its own, since only a tool call fires the PostToolUse hooks.
const BUNDLED_BUILD: MockChunkSpec = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      prompt: 'tool_call("Bash", {"command": "true"})\n',
      judgement_prompt: "verdict('pass', 'the mock harness ran the bundle hooks')\n",
      choices: [{ name: 'pass', description: 'hooks ran', to: 'done' }],
      retries_max: 1,
    },
  },
  work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
};

test('test_a_bundled_runners_mock_worker_runs_the_operators_and_the_runners_hooks', async ({ mockHub, runnerHost }) => {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const seeded = await mockHub.seed.chunk(BUNDLED_BUILD);
  expect(seeded.status).toBe(201);
  const chunkId: string = seeded.body.chunk_id;
  const status = async (): Promise<string> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.status as string;

  const landed = await stepUntil(runnerHost, status, (current) => current === 'done');
  expect(landed).toBe('done');

  expect(runnerHost.workerLog('operator-hook.log'), "the operator's hook never fired: the composed settings did not reach the worker").toBeDefined();
  expect(runnerHost.workerLog('heartbeats.log'), "the runner's heartbeat hook was lost in composition").toContain('runner heartbeat');
});
