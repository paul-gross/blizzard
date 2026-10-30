import { assertNoLeaks, assertPlatformNesting, assertSkeleton, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type RunnerHost, type StepExpect } from '../../harness/fixtures';

const branch = 'feature/scripted-build';

function graph(host: RunnerHost, repo: string): string {
  const cli = JSON.stringify(host.cliBinary);
  const build = `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'work-items', os.environ['BLIZZARD_CHUNK_ID']], check=True, capture_output=True)\n(repo / 'CONFLICTED.md').write_text('armed conflict\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change the armed conflict lever will reject'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'committed; checks are green')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } }, retries: { max: 2, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict; back to build.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  } });
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'forge' | 'issue' | 'repository' | 'runnerHosts'>;
async function firstBounce({ api, forge, issue, repository, runnerHosts }: World) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: [repo] });
  const main = await host.originHead(repo.name, 'main');
  expect((await forge.call('POST', '/_levers/merge_conflict', { repo: `${repo.owner}/${repo.name}` })).status).toBe(200);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name) } })).status).toBe(201);
  const { chunk } = await issue('armed conflict', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (state) => state.bounces.length > 0 || ['done', 'stopped', 'needs_human'].includes(state.status));
  await expect.poll(async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, { timeout: 30000 }).toBe('running');
  const detail = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  const pulls = (await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`)).body as { merged: boolean }[];
  return { api, repo, host, chunk, detail, pulls, main };
}

test.describe('conflicted delivery', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('test_conflict_lands_zero_repos_and_routes_the_bounce_envelope_back_to_build', async ({ api, forge, issue, repository, runnerHosts }) => {
    test.setTimeout(120000);
    const state = await firstBounce({ api, forge, issue, repository, runnerHosts });
    expect(state.detail.status).toBe('running');
    expect(state.detail.current_node_name).toBe('build');
    expect(state.detail.bounces).toHaveLength(1);
    expect(state.detail.bounces[0].cause).toBe('conflict');
    expect((state.detail.artifacts as { name: string }[]).filter((item) => item.name === 'bounce-envelope')).toHaveLength(1);
    expect(state.detail.landed).toBe(false);
    expect(state.pulls.length).toBeGreaterThan(0);
    expect(state.pulls.some((pull) => pull.merged)).toBe(false);
    expect(await state.host.originHead(state.repo.name, 'main')).toBe(state.main);
  });
});

traced('test_conflict_lands_zero_repos_and_routes_the_bounce_envelope_back_to_build', async ({ api, forge, issue, repository, runnerHosts, traceSink, freshHub }) => {
  test.setTimeout(150000);
  const state = await firstBounce({ api, forge, issue, repository, runnerHosts });
  await stepUntil(state.host, async () => (await api.get(`/api/chunks/${state.chunk.chunk_id}`)).body.history as { choice_name: string }[], (history) => history.filter((item) => item.choice_name === 'pass').length >= 2);
  await expect.poll(() => traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span) && roleOf(span) === 'step').length, { timeout: 30000 }).toBeGreaterThanOrEqual(3);
  const fleet = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  const traces = tracesOf(fleet).slice(0, 3);
  assertSkeleton(traces, [
    { name: 'step build', outcome: 'transitioned', toNode: 'deliver', children: ['queue wait', 'claim'] },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'build', children: ['hub exec'], events: ['bounce'], link: 'next', attributes: { 'blizzard.bounce.cause': 'conflict' } },
    { name: 'step build', outcome: 'transitioned', toNode: 'deliver', link: 'bounce' },
  ] satisfies StepExpect[]);
  expect(traces[1]!.root.events.find((event) => event.name === 'bounce')?.attributes['blizzard.bounce.cause']).toBe('conflict');
  const traceId = traces[0]!.root.traceId;
  await expect.poll(() => {
    const all = traceSink.spans().filter((span) => span.traceId === traceId);
    try { assertPlatformNesting(all.filter(isFleet), all.filter((span) => !isFleet(span))); return true; }
    catch { return false; }
  }, { timeout: 30000 }).toBe(true);
  const cutoff = BigInt(traces[2]!.root.endTimeUnixNano);
  const passes = traceSink.spans().filter((span) => span.traceId === traceId && span.name === 'hub action pass' && BigInt(span.startTimeUnixNano) < cutoff);
  expect(passes.length).toBeGreaterThan(0);
  const hubExecs = new Set(traces.flatMap((trace) => trace.children.filter((child) => child.name === 'hub exec').map((span) => span.spanId)));
  expect(passes.every((span) => span.parentSpanId !== null && hubExecs.has(span.parentSpanId) && span.attributes['blizzard.hub.action.name'] === 'land')).toBe(true);
  const tokens = (await state.host.mockSessions()).flatMap((session) => {
    const credential = session.spawn_credential as { lease_token?: string } | undefined;
    return credential?.lease_token ? [credential.lease_token] : [];
  });
  await state.host.stop();
  await freshHub.stop();
  assertNoLeaks(traceSink.spans(), { 'lease token': tokens, 'runner bearer': [state.host.hubTokens().runners['runner-local']!] });
});
