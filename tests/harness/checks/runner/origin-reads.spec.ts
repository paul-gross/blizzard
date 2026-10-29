import { expect, runnerTest as test } from '../../fixtures';
import type { RunnerHost } from '../../fixtures';

// A mock worker pushes two commits to a branch of the minted world's origin, one of them touching `LANDED.md` alone;
// the bare-origin reads are then asked of that push.

const BRANCH = 'feature/origin-reads';
const REPO = 'toy-api';
const MAX_STEPS = 40;

const COMMIT = "'git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Worker', 'commit', '-m'";
const BUILD = [
  'import os, pathlib, subprocess',
  `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${REPO}')`,
  `subprocess.run(['git', '-C', repo, 'switch', '-C', '${BRANCH}'], check=True, capture_output=True)`,
  "(pathlib.Path(repo) / 'OTHER.md').write_text('an unrelated file\\n')",
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True, capture_output=True)",
  `subprocess.run([${COMMIT}, 'feat: add an unrelated file'], check=True, capture_output=True)`,
  "(pathlib.Path(repo) / 'LANDED.md').write_text('landed by the mock worker\\n')",
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True, capture_output=True)",
  `subprocess.run([${COMMIT}, 'feat: land a change'], check=True, capture_output=True)`,
  `subprocess.run(['git', '-C', repo, 'push', 'origin', '${BRANCH}'], check=True, capture_output=True)`,
  '',
].join('\n');

const graph = {
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

async function stepUntilPushed(host: RunnerHost): Promise<string> {
  for (let pass = 0; pass < MAX_STEPS; pass++) {
    const head = await host.originHead(REPO, BRANCH);
    if (head !== undefined) return head;
    await host.step();
  }
  throw new Error(`the worker's push did not reach the origin within ${MAX_STEPS} passes\n${host.logTail()}`);
}

test('the reads see a worker’s push by branch and by the sha originHead answers', async ({ mockHub, runnerHost }) => {
  await mockHub.seed.chunk(graph as never);
  const head = await stepUntilPushed(runnerHost);

  for (const ref of [BRANCH, head]) {
    const tree = await runnerHost.originTree(REPO, ref);
    expect(tree, ref).toEqual(expect.arrayContaining(['LANDED.md', 'OTHER.md']));

    const history = await runnerHost.originLog(REPO, ref);
    expect(history?.slice(0, 2).map((commit) => commit.subject), ref).toEqual(['feat: land a change', 'feat: add an unrelated file']);
    expect(history?.[0]?.sha, ref).toBe(head);

    const landed = await runnerHost.originLog(REPO, ref, 'LANDED.md');
    expect(landed?.map((commit) => commit.subject), ref).toEqual(['feat: land a change']);

    expect(await runnerHost.originFile(REPO, ref, 'LANDED.md'), ref).toBe('landed by the mock worker\n');
  }
  expect(await runnerHost.originTree(REPO, 'main')).not.toContain('LANDED.md');
});

test('a repo, ref or path that is not there reads as undefined, and a path with no history as none', async ({ runnerHost }) => {
  expect(await runnerHost.originTree('no-such-repo', 'main')).toBeUndefined();
  expect(await runnerHost.originTree(REPO, 'no-such-branch')).toBeUndefined();
  expect(await runnerHost.originLog(REPO, 'no-such-branch')).toBeUndefined();
  expect(await runnerHost.originLog(REPO, 'main', 'no-such-file.md')).toEqual([]);
  expect(await runnerHost.originFile(REPO, 'no-such-branch', 'README.md')).toBeUndefined();
  expect(await runnerHost.originFile(REPO, 'main', 'no-such-file.md')).toBeUndefined();
  expect(await runnerHost.originFile('no-such-repo', 'main', 'README.md')).toBeUndefined();
});
