import { expect, type RunnerHost, stepChunkUntil, test } from '../../harness/fixtures';

// A runner's configured gate set covers only nodes the runner executes. The hub reports a gate it can never see a runner
// reach as not enforceable, and the runner lists the gate decisions it imposed through its own routes.

const RUNNER = 'runner-local';
const branch = 'feature/scripted-build';

function graph(host: RunnerHost, repo: string): string {
  const cli = JSON.stringify(host.cliBinary);
  const build = `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n(repo / 'LANDED.md').write_text('landed behind a runner gate\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a gated change'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'built and committed')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  } });
}

test.describe('gate enforceability', () => {
  test.use({ isolation: 'fresh-tenant', runnerConfig: { runner: [{ name: RUNNER, gates: ['deliver', 'no-such-node', 'build'] }] } });

  test('test_registry_read_flags_a_gate_no_graph_runs_on_a_runner', async ({ api, repository, runnerHosts }) => {
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name) } })).status).toBe(201);
    expect((await host.step()).outcome).toBe('completed');
    const id = host.runners[RUNNER]!.id;
    const read = await api.get(`/api/runners/${id}`);
    expect(read.status).toBe(200);
    expect(read.body.gates).toEqual(['deliver', 'no-such-node', 'build']);
    expect(read.body.unenforceable_gates).toEqual([
      { node: 'deliver', reason: 'hub-executed' },
      { node: 'no-such-node', reason: 'unknown-node' },
    ]);
    const status = await host.runners[RUNNER]!.status();
    expect(status.body.unenforceable_gates).toEqual(read.body.unenforceable_gates);
  });

  test('test_runner_lists_its_own_open_gate_decision_and_the_fleet_route_is_owner_guarded', async ({ api, bearer, issue, repository, runnerHosts }) => {
    test.setTimeout(120000);
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name) } })).status).toBe(201);
    const { chunk } = await issue('gate', { repository: repo });
    await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
    const parked = await stepChunkUntil(host, api, chunk.chunk_id, (status) => ['waiting_on_human', 'done', 'needs_human'].includes(status));
    expect(parked).toBe('waiting_on_human');
    const id = host.runners[RUNNER]!.id;
    const token = host.hubTokens().runners[RUNNER]!;
    const own = await (await bearer(token)).get(`/api/fleet/runners/${id}/decisions`);
    expect(own.status).toBe(200);
    const decisions = own.body.decisions as { chunk_id: string; node_name: string }[];
    expect(decisions.map((d) => [d.chunk_id, d.node_name])).toEqual([[chunk.chunk_id, 'build']]);
    const other = await (await bearer(token)).get('/api/fleet/runners/rn_00000000000000000000000000/decisions');
    expect(other.status).toBe(403);
    const local = await host.runners[RUNNER]!.gateDecisions();
    expect(local.status).toBe(200);
    expect((local.body.decisions as { chunk_id: string }[]).map((d) => d.chunk_id)).toEqual([chunk.chunk_id]);
  });
});
