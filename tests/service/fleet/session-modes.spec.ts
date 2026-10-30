import { expect, stepUntil, test } from '../../harness/fixtures';

const build = (repo: string): string => [
  'import os, pathlib, subprocess',
  `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${repo}')`,
  "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/session-modes'], check=True)",
  "p = pathlib.Path(repo, 'BUILD.md')",
  "p.write_text((p.read_text() if p.exists() else '') + 'build pass\\n')",
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a build pass'], check=True)",
  "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
  `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', '${repo}', '--branch', branch, '--commit', commit], check=True)`,
].join('\n');

function graph(repo: string, pooled: boolean): string {
  return JSON.stringify({ name: 'default-delivery', entry: 'build',
    ...(pooled ? { sessions: { code: { model: ['blizzard:basic'], effort: 'medium' } } } : {}),
    nodes: {
      build: { executor: 'runner', prompt: build(repo), session: pooled ? 'fresh:code' : 'resume:build',
        judgement: { prompt: "verdict('pass', 'checks are green')\n", choices: {
          pass: { description: 'Committed and green.', to: 'review' },
        } }, retries: { max: 1, exhausted: 'escalate' } },
      review: { executor: 'runner', prompt: 'pass\n', session: pooled ? 'resume:code' : 'fresh',
        produces: ['review-findings'], judgement: { prompt: [
          'import pathlib',
          "m = pathlib.Path('.review-count')",
          'n = (int(m.read_text()) if m.exists() else 0) + 1',
          'm.write_text(str(n))',
          "verdict('fail' if n == 1 else 'pass', 'scripted review')",
        ].join('\n'), choices: {
          pass: { description: 'Passes review.', to: 'deliver' },
          fail: { description: 'Blocking issues found.', to: 'build' },
        } }, retries: { max: 1, exhausted: 'escalate' } },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' },
        conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' },
        'inherited-failure': { description: 'Base red.', to: 'build' },
      } } },
    },
  });
}

test.use({ isolation: 'fresh-tenant' });

test('test_session_modes_resume_targeted_and_fresh_across_a_cycle', async ({ api, issue, repository, runnerHosts }) => {
  test.slow();
  const target = await repository();
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(target.name, false) } })).status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: target.name, url: target.origin }],
  }] } });
  const { chunk } = await issue('session modes', { repository: target });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
    (body) => body.status === 'done' || body.status === 'needs_human', 80);
  expect(detail.status).toBe('done');
  expect(detail.landed).toBe(true);

  const response = await host.runners['runner-local']!.leasesForChunk(id);
  expect(response.status).toBe(200);
  const leases = response.body.items;
  expect(leases.map((lease) => lease.created_at)).toEqual(leases.map((lease) => lease.created_at).sort());
  expect(leases.map((lease) => lease.node_name)).toEqual(['build', 'review', 'build', 'review']);
  const builds = leases.filter((lease) => lease.node_name === 'build').map((lease) => lease.session_id);
  const reviews = leases.filter((lease) => lease.node_name === 'review').map((lease) => lease.session_id);
  expect(builds[0]).toEqual(expect.any(String));
  expect(builds[1]).toBe(builds[0]);
  expect(new Set(reviews).size).toBe(2);
  expect(reviews.every((session) => session && session !== builds[0])).toBe(true);
  expect(leases.at(-1)!.session_id).toBe(reviews[1]);
  expect(leases.at(-1)!.session_id).not.toBe(builds[0]);

  const sessions = await host.mockSessions();
  const resumed = sessions.find((session) => session.session_id === builds[0]);
  expect(resumed?.turns).toEqual(expect.any(Number));
  expect(resumed!.turns as number).toBeGreaterThan(2);
  for (const id of reviews) {
    expect(sessions.find((session) => session.session_id === id)?.turns).toBeGreaterThanOrEqual(1);
  }
});

test('test_a_named_pool_threads_one_session_across_nodes_and_reasserts_its_model', async ({ api, issue, repository, runnerHosts }) => {
  test.slow();
  const target = await repository();
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(target.name, true) } })).status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: target.name, url: target.origin }],
  }] } });
  const { chunk } = await issue('pooled sessions', { repository: target });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
    (body) => body.status === 'done' || body.status === 'needs_human', 80);
  expect(detail.status).toBe('done');
  expect(detail.landed).toBe(true);

  const response = await host.runners['runner-local']!.leasesForChunk(id);
  expect(response.status).toBe(200);
  const leases = response.body.items;
  expect(leases.map((lease) => lease.created_at)).toEqual(leases.map((lease) => lease.created_at).sort());
  expect(leases.map((lease) => lease.node_name)).toEqual(['build', 'review', 'build', 'review']);
  const [first, firstReview, second, secondReview] = leases;
  expect(first!.session_id).toEqual(expect.any(String));
  expect(second!.session_id).toEqual(expect.any(String));
  expect(second!.session_id).not.toBe(first!.session_id);
  expect(firstReview!.session_id).toBe(first!.session_id);
  expect(secondReview!.session_id).toBe(second!.session_id);
  expect(new Set(leases.map((lease) => lease.session_id))).toEqual(new Set([first!.session_id, second!.session_id]));
  for (const lease of leases) {
    expect([lease.resolved_model, lease.resolved_effort]).toEqual(['sonnet', 'medium']);
  }

  const sessions = await host.mockSessions();
  for (const id of [first!.session_id, second!.session_id]) {
    const session = sessions.find((item) => item.session_id === id);
    expect(session).toBeDefined();
    const invocations = session!.invocations as { kind: string; model: string | null; effort: string | null }[];
    const mints = invocations.filter((invocation) => invocation.kind === 'spawn');
    const resumes = invocations.filter((invocation) => invocation.kind === 'resume');
    expect(mints.length).toBeGreaterThan(0);
    expect(resumes.length).toBeGreaterThan(0);
    expect(mints.every((invocation) => invocation.model === 'sonnet')).toBe(true);
    expect(resumes.every((invocation) => invocation.model === mints[0]!.model)).toBe(true);
    expect(invocations.every((invocation) => invocation.effort === 'medium')).toBe(true);
  }
});
