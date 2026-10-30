import { assertInvocations, assertRunnerNesting, expect, isFleet, roleOf, stepUntil, test, traced, type RunnerHost, type WorkerCheckpoint } from '../../harness/fixtures';

const branch = 'feature/mixed-lineage';
const buildFile = 'LANDED-MIXED-BUILD.md';
const reviewFile = 'LANDED-MIXED-OPENCODE.md';
const buildModel = 'claude-mixed-build';
const reviewModel = 'mock-provider/opencode-mixed-review';

function graph(host: RunnerHost, repo: string, checkpoint: WorkerCheckpoint): string {
  const cli = JSON.stringify(host.cliBinary);
  const script = (file: string, tool: string): string => `import os, pathlib, subprocess\n${tool}\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n(repo / ${JSON.stringify(file)}).write_text('landed by the mixed harness\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land ${file}'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  const build = script(buildFile, "tool_call('Skill', {'skill': 'wf-mixed-review'}, output='ran the commit skill')");
  const review = script(reviewFile, "tool_call('task', {'subagent_type': 'mixed-harness-reviewer'}, output='spawned a review sub-agent')") + checkpoint.snippet('after-declare');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', sessions: {
    'mixed-harness-review': { harnesses: ['opencode'], effort: 'high', model: [reviewModel] },
  }, nodes: {
    build: { executor: 'runner', prompt: build, produces: [{ name: 'commit', kind: 'git_commit' }], judgement: { prompt: "verdict('pass', 'the claude code build committed; checks are green')\n", choices: { pass: { description: 'Committed.', to: 'opencode-review' } } }, retries: { max: 1, exhausted: 'escalate' } },
    'opencode-review': { executor: 'runner', session: 'resume:mixed-harness-review', prompt: review, produces: [{ name: 'commit', kind: 'git_commit' }], judgement: { prompt: "verdict('pass', 'the opencode review committed; checks are green')\n", choices: { pass: { description: 'Reviewed.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  } });
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'issue' | 'repository' | 'runnerHosts' | 'runnerConfig' | 'workerCheckpoint'>;
async function traverse({ api, issue, repository, runnerHosts, runnerConfig, workerCheckpoint }: World) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! }, runnerConfig: { ...runnerConfig, transcripts: { ship: true }, runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] } });
  const minted = await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name, workerCheckpoint) } });
  expect(minted.status, JSON.stringify(minted.body)).toBe(201);
  const nodeIds = Object.fromEntries((minted.body.nodes as { name: string; node_id: string }[]).map((node) => [node.name, node.node_id]));
  const { chunk } = await issue('mixed-harness lineage', { repository: repo });
  expect((await api.patch(`/api/chunks/${chunk.chunk_id}`, { data: { default_model: [buildModel], default_effort: 'medium' } })).status).toBe(202);
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const runner = host.runners['runner-local']!;
  const leases = async () => (await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunk.chunk_id);
  const buildLease = await stepUntil(host, async () => (await leases()).find((lease) => lease.node_name === 'build'), (found) => found !== undefined);
  // The judge invocation records usage before a later completed host pass closes build and mints the next lineage.
  await stepUntil(host, async () => (await runner.leaseUsage(buildLease!.lease_id)).body.items.some((fact) => fact.kind === 'judge'), Boolean);
  const firstPid = host.pid;
  await host.stop();
  await host.restart();
  let arrived = false;
  void workerCheckpoint.reached('after-declare').then(() => { arrived = true; });
  await stepUntil(host, async () => arrived, Boolean);
  const mid = (await leases()).find((lease) => lease.node_name === 'opencode-review');
  expect(mid).toBeDefined();
  const midHead = await host.originHead(repo.name, branch);
  const secondPid = host.pid;
  await host.stop();
  await host.restart();
  workerCheckpoint.release('after-declare');
  const status = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (value) => ['done', 'stopped', 'needs_human'].includes(value));
  return { repo, host, chunk, runner, leases: await leases(), mid, midHead, firstPid, secondPid, thirdPid: host.pid, status, nodeIds };
}

test.describe('mixed harness', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('test_mixed_lineage_crosses_a_harness_boundary_and_survives_two_operator_restarts', async ({ api, issue, repository, runnerHosts, runnerConfig, workerCheckpoint }) => {
    test.setTimeout(150000);
    const state = await traverse({ api, issue, repository, runnerHosts, runnerConfig, workerCheckpoint });
    expect(state.status).toBe('done');
    expect(new Set([state.firstPid, state.secondPid, state.thirdPid]).size).toBe(3);
    expect(state.mid?.git_commits.length).toBeGreaterThan(0);
    expect(state.midHead).toBeTruthy();
    const builds = state.leases.filter((lease) => lease.node_name === 'build');
    const reviews = state.leases.filter((lease) => lease.node_name === 'opencode-review');
    expect(builds).toHaveLength(1);
    expect(reviews).toHaveLength(1);
    expect(builds[0]).toMatchObject({ harness_id: 'claude_code', resolved_model: buildModel, resolved_effort: 'medium' });
    expect(reviews[0]).toMatchObject({ lease_id: state.mid?.lease_id, epoch: state.mid?.epoch, session_id: state.mid?.session_id, harness_id: 'opencode', resolved_model: reviewModel, resolved_effort: 'high' });
    expect(builds[0]!.session_id).toBeTruthy();
    expect(reviews[0]!.session_id).toBeTruthy();
    expect(reviews[0]!.session_id).not.toBe(builds[0]!.session_id);
    expect(builds[0]!.harness_version).toBeTruthy();
    expect(reviews[0]!.harness_version).toBeTruthy();
    for (const file of [buildFile, reviewFile]) expect(await state.host.originLog(state.repo.name, 'main', file)).toHaveLength(1);
    const detail = (await api.get(`/api/chunks/${state.chunk.chunk_id}`)).body;
    const usage = detail.usage as { node_id: string; harness_id: string; model: string; harness_version: string }[];
    for (const [node, lease, harness] of [['build', builds[0]!, 'claude_code'], ['opencode-review', reviews[0]!, 'opencode']] as const) {
      const runnerUsage = (await state.runner.leaseUsage(lease.lease_id)).body.items;
      const boardUsage = usage.filter((row) => row.node_id === state.nodeIds[node]);
      expect(runnerUsage.length).toBeGreaterThan(0);
      expect(boardUsage.length).toBeGreaterThan(0);
      expect(new Set(boardUsage.map((row) => row.harness_id))).toEqual(new Set([harness]));
      expect(new Set(boardUsage.map((row) => row.model))).toEqual(new Set(runnerUsage.map((row) => row.model)));
      expect(new Set(boardUsage.map((row) => row.harness_version))).toEqual(new Set([lease.harness_version]));
    }
    let remaining = 1;
    for (let pass = 0; pass < 10 && remaining > 0; pass++) {
      const derived = await api.post('/api/analytics/re-derive', { data: { chunk_id: state.chunk.chunk_id, limit: 200 } });
      expect(derived.status).toBe(200);
      remaining = derived.body.remaining as number;
    }
    expect(remaining).toBe(0);
    const events = ((await api.get('/api/analytics/events', { params: { limit: 1000 } })).body.events as { chunk_id: string; node_id: string; kind: string; subject: string; harness_id: string; effort: string; model: string }[]).filter((event) => event.chunk_id === state.chunk.chunk_id);
    const skill = events.find((event) => event.node_id === state.nodeIds.build && event.kind === 'skill_invocation');
    const spawn = events.find((event) => event.node_id === state.nodeIds['opencode-review'] && event.kind === 'agent_spawn');
    expect(skill).toMatchObject({ subject: 'wf-mixed-review', harness_id: 'claude_code', effort: 'medium', model: buildModel });
    expect(spawn).toMatchObject({ subject: 'mixed-harness-reviewer', harness_id: 'opencode', effort: 'high', model: reviewModel });
  });
});

traced('test_mixed_lineage_crosses_a_harness_boundary_and_survives_two_operator_restarts', async ({ api, issue, repository, runnerHosts, runnerConfig, workerCheckpoint, traceSink }) => {
  test.setTimeout(150000);
  const state = await traverse({ api, issue, repository, runnerHosts, runnerConfig, workerCheckpoint });
  await expect.poll(() => {
    const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
    return [spans.filter((span) => roleOf(span) === 'step').length, spans.filter((span) => roleOf(span) === 'runner/invocation').length];
  }, { timeout: 30000 }).toEqual([3, 5]);
  const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  assertInvocations(assertRunnerNesting(spans, 2), {
    build: { harnessId: 'claude_code', responseModel: 'claude-opus-4-8', requestModel: buildModel },
    'opencode-review': { harnessId: 'opencode', responseModel: reviewModel, requestModel: reviewModel },
  });
});
