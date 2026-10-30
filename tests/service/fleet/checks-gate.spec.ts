import { expect, stepUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

for (const [title, judgement] of [
  ['test_checks_gate_bounces_a_red_pass_then_lands_when_green', "verdict('pass', 'submitting for the gate to judge against the checks')\n"],
  ['test_a_red_check_through_a_non_gated_fail_routes_normally', "import pathlib\nn = int(pathlib.Path('.build-count').read_text())\nif n == 1:\n    verdict('fail', 'a check is red; routing back through the non-gated fail')\nelse:\n    verdict('pass', 'checks are green now')\n"],
] as const) {
  test(title, async ({ repository, issue, api, runnerHosts }) => {
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
            "c = pathlib.Path('.build-count')",
            "n = (int(c.read_text()) if c.exists() else 0) + 1",
            'c.write_text(str(n))',
            "if n >= 2: pathlib.Path('.checks-ok').write_text('ok')",
            "p = pathlib.Path(repo) / 'CHECKS.md'",
            "p.write_text((p.read_text() if p.exists() else '') + f'build {n}\\n')",
            "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
            "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a build pass'], check=True)",
            "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
            "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/scripted-build'], check=True)",
            "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/scripted-build', '--commit', sha], check=True)",
            '',
          ].join('\n'),
          checks: ['test -f .checks-ok'],
          judgement: { prompt: judgement, choices: { pass: { description: 'Committed and green.', to: 'deliver', requires_checks: true }, fail: { description: 'Not ready.', to: 'build' } } },
          retries: { max: 3, exhausted: 'escalate' },
        },
        deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Delivered.', to: 'done' }, conflict: { description: 'Conflicted.', to: 'build' }, failure: { description: 'Failed to deliver.', to: 'build' }, 'inherited-failure': { description: 'The base check stays red.', to: 'build' } } } },
      },
    };
    expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } })).status).toBe(201);
    const { chunk } = await issue(title, { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (state) => state.status === 'done');
    if (title === 'test_a_red_check_through_a_non_gated_fail_routes_normally') {
      expect(detail.history.map((entry: { choice_name: string }) => entry.choice_name)).toEqual(expect.arrayContaining(['fail', 'pass']));
    }
    expect((await host.originFile(repo.name, 'main', 'CHECKS.md'))?.match(/build /g)).toHaveLength(2);
  });
}
