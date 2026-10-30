import { expect, stepUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_authored_landed_edge_runs_a_post_merge_node_after_landing', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const graph = {
    name: 'default-delivery', entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${repo.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
          "(pathlib.Path(repo) / 'LANDED.md').write_text('landed by the mock harness\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change from the mock harness'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/scripted-build'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/scripted-build', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed the change; checks are green')\n", choices: { pass: { description: 'Committed and green.', to: 'deliver' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: {
        landed: { description: 'Landed; run the post-merge acceptance check.', to: 'verify' },
        conflict: { description: 'Conflict; back to build.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' },
        'inherited-failure': { description: 'The base check stays red.', to: 'build' },
      } } },
      verify: {
        executor: 'runner', prompt: 'pass\n',
        judgement: { prompt: "verdict('pass', 'the post-merge acceptance check is green')\n", choices: { pass: { description: 'Green.', to: 'done' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
    },
  };
  expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } })).status).toBe(201);
  const { chunk } = await issue('post-merge node', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (state) => state.status === 'done');
  expect(detail.status).toBe('done');
  const history = detail.history as { from_node_name: string; to_node_name: string | null; to_node_id: string; choice_name: string }[];
  expect(history.map((t) => [t.from_node_name, t.to_node_name, t.choice_name])).toEqual(expect.arrayContaining([
    ['build', 'deliver', 'pass'], ['deliver', 'verify', 'landed'],
  ]));
  expect(history.some((t) => t.from_node_name === 'verify' && t.to_node_id === 'done' && t.choice_name === 'pass')).toBe(true);
  expect(history.findIndex((t) => t.from_node_name === 'deliver' && t.choice_name === 'landed')).toBeLessThan(history.findIndex((t) => t.from_node_name === 'verify' && t.choice_name === 'pass'));
  expect(detail.landed).toBe(true);
  const pulls = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`);
  expect(pulls.status).toBe(200);
  expect((pulls.body as { merged: boolean }[]).some((pull) => pull.merged)).toBe(true);
  expect(await host.originTree(repo.name, 'main')).toContain('LANDED.md');
});
