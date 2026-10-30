import { expect, stepUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_basic_workspace_chunk_lands_without_winter', async ({ repository, issue, api, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerWinterTrap: true, runnerRepositories: { [repo.name]: repo.origin! } });
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
        judgement: { prompt: "verdict('pass', 'committed')\n", choices: { pass: { description: 'committed', to: 'review' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      review: {
        executor: 'runner', prompt: 'pass\n', session: 'fresh', produces: ['review-findings'],
        judgement: { prompt: "verdict('pass', 'ready to deliver')\n", choices: { pass: { description: 'approved', to: 'deliver' }, fail: { description: 'blocking issues', to: 'build', prompt_addendum: '# address the review findings\n' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'landed', to: 'done' }, failure: { description: 'failure', to: 'build' }, conflict: { description: 'conflict', to: 'build' }, 'inherited-failure': { description: 'base failure', to: 'build' } } } },
    },
  };
  const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const { chunk } = await issue('basic workspace', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');
  expect(host.workspaceLayout(chunk.chunk_id, repo.name)).toEqual({ landed: true, project: true, winter: false });
  expect(await host.originTree(repo.name, 'main')).toContain('LANDED.md');
  expect(host.winterInvoked()).toBe(false);
});
