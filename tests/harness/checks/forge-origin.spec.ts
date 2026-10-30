import { expect, stepUntil, test } from '../fixtures';

test.use({ isolation: 'fresh-tenant' });

test('a runner push and a forge landing share the test repository without changing its neighbour', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const target = await repository();
  const neighbour = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [target.name]: target.origin!, [neighbour.name]: neighbour.origin! } });
  const neighbourHead = await host.originHead(neighbour.name, 'main');
  const graph = {
    name: 'default-delivery', entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${target.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feat/forge-origin'], check=True)",
          "(pathlib.Path(repo) / 'LANDED.md').write_text('from the runner\\n')",
          "subprocess.run(['git', '-C', repo, 'add', 'LANDED.md'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Worker', 'commit', '-m', 'feat: landed'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feat/forge-origin'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feat/forge-origin', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed')\n", choices: { pass: { description: 'committed', to: 'deliver' } } },
        retries: { max: 2, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'landed', to: 'done' }, failure: { description: 'failure', to: 'build' }, conflict: { description: 'conflict', to: 'build' }, 'inherited-failure': { description: 'base failure', to: 'build' } } } },
    },
  };
  const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const { chunk } = await issue('shared bare origin', { repository: target });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');

  const landed = await host.originHead(target.name, 'main');
  expect(await host.originFile(target.name, 'main', 'LANDED.md'), JSON.stringify((await api.get(`/api/chunks/${chunk.chunk_id}`)).body)).toBe('from the runner\n');
  const prs = await forge.call('GET', `/repos/${target.owner}/${target.name}/pulls?state=all`);
  expect((prs.body as { merged: boolean }[]).some((pr) => pr.merged)).toBe(true);
  const history = await host.originLog(target.name, 'main');
  expect(history?.[0]?.sha).toBe(landed);
  expect(history?.[0]?.parents.length).toBeGreaterThan(0);
  expect(await host.originHead(neighbour.name, 'main')).toBe(neighbourHead);
  expect(await host.originFile(neighbour.name, 'main', 'LANDED.md')).toBeUndefined();
});
