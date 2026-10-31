import { expect, stepUntil, test } from '../../../harness/fixtures';

const REPO = 'toy-api';
const LANDED = 'ACTION_LANDED.md';
const BUILD = [
  'import os, pathlib, subprocess',
  "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / 'toy-api')",
  "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
  `pathlib.Path(repo, '${LANDED}').write_text('landed by the land action\\n')`,
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change the land action lands'], check=True)",
  "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
  "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', 'toy-api', '--branch', branch, '--commit', commit], check=True)",
].join('\n');

const TEST_NODES = {
  pick: { executor: 'hub', action: 'test.answer', with: { choice: 'onward' }, judgement: { choices: {
    onward: { description: 'On to walk.', to: 'walk' }, failure: { description: 'Failed.', to: 'build' },
  } } },
  walk: { executor: 'hub', action: 'test.sequence', with: { choices: ['pending', 'done'] }, poll_interval: 1, poll_timeout: 600,
    judgement: { choices: { done: { description: 'Walked.', to: 'done' }, failure: { description: 'Failed.', to: 'build' } } } },
};

function actionGraph(prompt: string, nodes: Record<string, unknown>, first: string): string {
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'the mock harness did its turn')\n", choices: {
      pass: { description: 'Built.', to: first },
    } }, retries: { max: 1, exhausted: 'escalate' } },
    ...nodes,
  } });
}

test.use({ isolation: 'fresh-tenant' });

test('test_land_lands_a_chunk_against_the_mock_forge', async ({ api, issue, repository, runnerHosts, forge }) => {
  test.slow();
  expect((await api.post('/api/graphs', { data: { definition_yaml: actionGraph(BUILD, { deliver: {
    executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Every repo merged.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base red.', to: 'build' },
    } },
  } }, 'deliver') } })).status).toBe(201);
  const target = await repository(REPO);
  const repoFull = `${target.owner}/${target.name}`;
  const ref = async () => ((await forge.call('GET', `/repos/${repoFull}/git/ref/heads/main`)).body as { object: { sha: string } }).object.sha;
  const before = await ref();
  const runnerHost = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: REPO, url: target.origin }],
  }] } });
  const { chunk } = await issue('actions', { repository: target });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (body) => body.status === 'done', 80);
  expect(detail.landed).toBe(true);
  const after = await ref();
  expect(after).toBeTruthy();
  expect(after).not.toBe(before);
  const landed = detail.landed_repos as { repo: string; commit_hash: string }[];
  expect(landed).toEqual([expect.objectContaining({ repo: repoFull, commit_hash: after })]);
  expect(detail.open_prs).toEqual([]);
  const pulls = (await forge.call('GET', `/repos/${landed[0]!.repo}/pulls?state=all`)).body as { merged: boolean }[];
  expect(pulls).toHaveLength(1);
  expect(pulls[0]!.merged).toBe(true);
  const comparison = (await forge.call('GET', `/repos/${repoFull}/compare/${before}...${after}`)).body as { files: { filename: string }[]; commits: unknown[] };
  expect(comparison.files.map((file) => file.filename)).toContain(LANDED);
  expect(comparison.commits).toHaveLength(2);
});

test('test_test_actions_drive_hub_nodes', async ({ api, issue, runnerHost }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: actionGraph('pass\n', TEST_NODES, 'pick') } })).status).toBe(201);
  const { chunk } = await issue('test actions');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (body) => body.status === 'done', 80);
  expect((detail.history as { choice_name: string }[]).map((item) => item.choice_name).slice(-2)).toEqual(['onward', 'done']);
  expect(Object.fromEntries((detail.artifacts as { name: string; content: string }[]).filter((item) => item.name.startsWith('test-sequence/')).map((item) => [item.name, item.content]))).toEqual({
    'test-sequence/0': 'pending', 'test-sequence/1': 'done',
  });
});
