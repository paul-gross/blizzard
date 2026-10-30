import { assertIdentity, assertSkeleton, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type RunnerHost, type StepExpect } from '../../harness/fixtures';

const branch = 'feature/scripted-build';

function graph(host: RunnerHost, repo: string): string {
  const cli = JSON.stringify(host.cliBinary);
  const build = `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n(repo / 'LANDED.md').write_text('landed behind a human gate\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change awaiting sign-off'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'built and committed; awaiting human sign-off')\n", choices: { pass: { description: 'Committed.', to: 'approve-gate' } } }, retries: { max: 1, exhausted: 'escalate' } },
    'approve-gate': { executor: 'runner', judgement: { by: 'human', choices: { approve: { description: 'Ship it.', to: 'deliver' }, reject: { description: 'Return to build.', to: 'build' } } } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  } });
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'cli' | 'forge' | 'issue' | 'repository' | 'runnerHosts'>;
async function gated({ api, cli, forge, issue, repository, runnerHosts }: World) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name) } })).status).toBe(201);
  const { chunk } = await issue('gate', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  const parked = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => ['waiting_on_human', 'done', 'needs_human'].includes(status));
  const detail = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  const decision = detail.decision as { decision_id: string; node_name: string; choices: { name: string }[] };
  const listed = await cli(['hub', 'decision', 'list']);
  const resolved = await cli(['hub', 'decision', 'resolve', decision.decision_id, 'approve', '--by', 'alice', '--json']);
  const landed = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => ['done', 'needs_human', 'stopped'].includes(status));
  const open = (await api.get('/api/decisions')).body.decisions as { decision_id: string }[];
  const pulls = (await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`)).body as { merged: boolean }[];
  return { repo, host, chunk, parked, detail, decision, listed, resolved, landed, open, pulls };
}

test.describe('human gate', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('test_graph_gate_parks_a_decision_then_decide_delivers', async ({ api, cli, forge, issue, repository, runnerHosts }) => {
    test.setTimeout(120000);
    const state = await gated({ api, cli, forge, issue, repository, runnerHosts });
    expect(state.parked).toBe('waiting_on_human');
    expect(state.decision.node_name).toBe('approve-gate');
    expect(state.decision.choices.map((choice) => choice.name).sort()).toEqual(['approve', 'reject']);
    expect((state.detail.artifacts as { kind: string }[]).some((artifact) => artifact.kind === 'git_commit')).toBe(true);
    expect(state.listed.code, state.listed.stderr).toBe(0);
    expect(state.listed.stdout).toContain(state.decision.decision_id);
    expect(state.resolved.code, state.resolved.stderr).toBe(0);
    expect(JSON.parse(state.resolved.stdout).resolved_at).toBeTruthy();
    expect(state.landed).toBe('done');
    expect(state.open).toEqual([]);
    expect(state.pulls.some((pull) => pull.merged)).toBe(true);
    expect(await state.host.originTree(state.repo.name, 'main')).toContain('LANDED.md');
  });
});

traced('test_graph_gate_parks_a_decision_then_decide_delivers', async ({ api, cli, forge, issue, repository, runnerHosts, traceSink }) => {
  test.setTimeout(120000);
  const state = await gated({ api, cli, forge, issue, repository, runnerHosts });
  await expect.poll(() => traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span) && ['step', 'gate'].includes(roleOf(span))).length, { timeout: 30000 }).toBe(3);
  const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  const traces = tracesOf(spans);
  assertIdentity(traces, { decisionIds: [state.decision.decision_id] });
  assertSkeleton(traces, [
    { name: 'step build', outcome: 'transitioned', toNode: 'approve-gate', children: ['queue wait', 'claim'] },
    { name: 'gate approve-gate', outcome: 'decided', toNode: 'deliver', children: ['decision pickup'], link: 'next' },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'done', children: ['hub exec'], link: 'next' },
  ] satisfies StepExpect[]);
  const resolvedAt = JSON.parse(state.resolved.stdout).resolved_at as string;
  const timestamp = /^(.+?)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$/.exec(resolvedAt);
  expect(timestamp, resolvedAt).not.toBeNull();
  expect(traces[1]!.root.endTimeUnixNano).toBe((BigInt(Date.parse(`${timestamp![1]}${timestamp![3]}`)) * 1_000_000n + BigInt((timestamp![2] ?? '').padEnd(9, '0'))).toString());
});
