import { expect, runnerTest as test } from '../../fixtures';
import type { RunnerHost } from '../../fixtures';

// A chunk's life on a real runner host against a mock hub, driven only by steps: the host claims it, a mock worker
// commits and pushes, and the completion reaches the hub. Every wait below is a step of the host, never the clock.

const BRANCH = 'feature/round-trip';
const REPO = 'toy-api';
const MAX_STEPS = 40;

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

const buildThenDeliver = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      prompt: BUILD,
      judgement_prompt: "verdict('pass', 'the change is committed and green')\n",
      choices: [{ name: 'pass', description: 'committed and green', to: 'deliver' }],
    },
    deliver: {
      executor: 'hub',
      judgement: { choices: { landed: { description: 'Every repo merged cleanly.', to: 'done' } } },
    },
  },
};

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

test('a claimed chunk is worked by the mock worker, pushed, completed, and its lease read back', async ({ mockHub, runnerHost, runnerStream }) => {
  const runner = runnerHost.runners['runner-local']!;
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const chunkId = (await mockHub.seed.chunk(buildThenDeliver as never)).body.chunk_id;
  const status = async (): Promise<string> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.status as string;
  const stream = await runnerStream(runnerHost.url, { fromNow: true });

  await stepUntil(runnerHost, status, (current) => current === 'done');

  expect(await status()).toBe('done');
  const head = await runnerHost.originHead(REPO, BRANCH);
  expect(head).toMatch(/^[0-9a-f]{40}$/);
  expect(head).not.toBe(await runnerHost.originHead(REPO, 'main'));
  const paths = (await mockHub.captured.read()).body.requests.map((request) => `${request.method} ${request.path}`);
  expect(paths).toContain('POST /api/fleet/routes');
  expect(paths).toContain('POST /api/fleet/events');
  const leases = (await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId);
  expect(leases).toHaveLength(1);
  expect(leases[0]).toMatchObject({ node_name: 'build', state: 'closed' });
  await stream.waitFor((frame) => frame.event === 'lease-changed');
});
