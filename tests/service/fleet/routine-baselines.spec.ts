import { chunkProgress, expect, stepUntil, test, type HubApi, type MockRunner, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

// `GET /api/routines/{routine_id}/baselines` over a real hub, counting what lands against a delivered garden run's
// recorded revision: the landing is a real `land` action merging a real runner host's commit through the forge, the
// one production path that records a repo landing, which the garden graph's own deliver node never takes. A landing
// names its repository `owner/name`, so the delta records its revision under that name. The baseline's shape without a
// landing is `tests/service/hub/routine-baselines.spec.ts`'s.

const SCOPE = 'garden-svc';

function addOp(locus: string) {
  return { op: 'add', class: 'stale-docstring', locus, summary: `weed at ${locus}` };
}

/** A runner node committing a change and handing it to a hub node taking the `land` action. */
function landingGraph(repo: Repository) {
  return {
    name: 'baselines-landing', entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${repo.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/baselines-land'], check=True)",
          "(pathlib.Path(repo) / 'BASELINES_LANDED.md').write_text('landed against the baseline\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change against the baseline'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/baselines-land'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/baselines-land', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed the change')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } },
        retries: { max: 2, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check failed.', to: 'build' },
      } } },
    },
  };
}

/** One routine run's whole delivery, walked by the mock runner to the `deliver` node, whose `garden-deliver` action records the delta; resolves to the choice the action answered. */
async function deliver(api: HubApi, runner: MockRunner, routineId: string, repo: Repository, head: string, ops: unknown[]) {
  const run = await api.post(`/api/routines/${routineId}/run`, { data: { scope_slug: SCOPE, mode: 'full', note: 'sweep' } });
  expect(run.status).toBe(201);
  const chunkId: string = run.body.chunk_id;
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);
  const delta = { name: 'delta', kind: 'asset', content: JSON.stringify({ scope: SCOPE, revisions: { [`${repo.owner}/${repo.name}`]: head }, measurement: 'service sweep', findings: ops }), attached: true };
  const surveyed = await runner.drive('complete', {
    chunk_id: chunkId, choice: 'found', artifacts: [{ name: 'survey', kind: 'asset', content: '{}', attached: true }, delta],
  });
  expect(surveyed.body.response?.outcome).toBe('next');
  const reconciled = await runner.drive('complete', { chunk_id: chunkId, choice: 'nothing-to-propose', artifacts: [delta] });
  expect(reconciled.body.response?.outcome).toBe('hub_node_taken');
  await expect
    .poll(async () => (await api.get(`/api/chunks/${chunkId}`)).body.current_node_name, { timeout: 60_000 })
    .not.toBe('deliver');
  const detail = await api.get(`/api/chunks/${chunkId}`);
  const exit = (detail.body.history as { from_node_name: string; choice_name: string | null }[])
    .filter((transition) => transition.from_node_name === 'deliver')
    .at(-1);
  return exit?.choice_name ?? undefined;
}

/** Steps the host until the chunk waits at the hub's `land` node, then reads the hub until it has landed. */
async function land(host: RunnerHost, api: HubApi, chunkId: string) {
  const read = async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunkId}`)).body;
  await stepUntil(host, read, (s) => s.landed || s.pending !== null || s.current_node_name === 'deliver', { progress: chunkProgress });
  await expect.poll(async () => (await read()).landed, { timeout: 60_000 }).toBe(true);
}

test.describe('a delivered garden run', () => {
  // The runner, the runner host and the repository the delivery needs are tenant-wide, so each test owns its tenant.
  test.use({ isolation: 'fresh-tenant' });

  test('test_baselines_serves_the_swept_scopes_recorded_revision', async ({ api, forge, repository, mockRunner, runnerHosts, issue }) => {
    test.slow();
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    const head: string = commit.body.sha;
    const created = await api.post('/api/routines', {
      data: { name: 'garden-service', graph_name: 'garden-routine', default_scope_slug: SCOPE, default_model: [], default_effort: null },
    });
    expect(created.status).toBe(201);
    const routineId: string = created.body.routine_id;
    const runner = await mockRunner('garden');
    expect((await runner.drive('register')).body.status).toBe(201);

    expect(await deliver(api, runner, routineId, repo, head, [addOp('src/app.py:0')])).toBe('recorded');
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    const graph = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(landingGraph(repo)) } });
    expect(graph.status, JSON.stringify(graph.body)).toBe(201);
    const { chunk } = await issue('baselines land', { repository: repo });
    expect((await api.patch(`/api/chunks/${chunk.chunk_id}`, { data: { graph_id: graph.body.graph_id } })).status).toBe(202);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    await land(host, api, chunk.chunk_id);

    const response = await api.get(`/api/routines/${routineId}/baselines`);

    expect(response.status).toBe(200);
    expect(response.body).toHaveLength(1);
    const [baseline] = response.body;
    expect(baseline.scope_slug).toBe(SCOPE);
    expect(baseline.finding_set_id).toMatch(/^fins_/);
    expect(baseline.recorded_at).toBeTruthy();
    expect(baseline.repos).toEqual([{ repo: `${repo.owner}/${repo.name}`, revision: head, landed_since: 1 }]);
  });
});
