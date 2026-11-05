import { chunkProgress, expect, stepUntil, test, type HubApi, type MockRunner, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

// Delivering an accepted garden proposal's minted work item closes the findings it answers to `delivered`: the
// delivery is a real `land` action merging a real runner host's commit through the forge, which a hub-tier mock runner
// cannot produce. The rest of the findings lifecycle is `tests/service/hub/finding-exits.spec.ts`'s.

const routine = 'garden-service';
const scope = 'garden-svc';

interface Garden {
  api: HubApi;
  runner: MockRunner;
  repository: Repository;
  head: string;
  routineId: string;
}

async function garden(api: HubApi, runner: MockRunner, repository: Repository, head: string): Promise<Garden> {
  const created = await api.post('/api/routines', { data: {
    name: routine, graph_name: 'garden-routine', default_scope_slug: scope, default_model: [], default_effort: null,
  } });
  expect(created.status).toBe(201);
  const registered = await runner.drive('register');
  expect(registered.body.status).toBe(201);
  return { api, runner, repository, head, routineId: created.body.routine_id };
}

/** What the deliver node's `garden-deliver` pass answered, and the refusal it recorded when it answered `invalid`. */
interface Delivered {
  outcome: string | undefined;
  detail: string;
}

/**
 * One routine run's whole delivery: mint the run, claim it as the mock runner and walk it through survey and reconcile
 * (and propose, when `proposals` are given) to the graph's `deliver` node, whose `garden-deliver` action records the
 * delta and docket. Resolves once the run has left `deliver`.
 */
async function deliver(g: Garden, ops: object[], options: { scope?: string; proposals?: object[]; routineId?: string } = {}): Promise<Delivered> {
  const targetScope = options.scope ?? scope;
  const proposals = options.proposals ?? [];
  const run = await g.api.post(`/api/routines/${options.routineId ?? g.routineId}/run`, {
    data: { scope_slug: targetScope, mode: 'full', note: 'sweep' },
  });
  expect(run.status).toBe(201);
  const chunkId = run.body.chunk_id;
  expect((await g.api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const claimed = await g.runner.drive('claim', { chunk_id: chunkId });
  expect(claimed.body.claimed).toBe(true);
  const delta = { name: 'delta', kind: 'asset', content: JSON.stringify({
    scope: targetScope, revisions: { [g.repository.name]: g.head }, measurement: 'service sweep', findings: ops,
  }), attached: true };
  const surveyed = await g.runner.drive('complete', {
    chunk_id: chunkId, choice: 'found', artifacts: [{ name: 'survey', kind: 'asset', content: '{}', attached: true }, delta],
  });
  expect(surveyed.body.response?.outcome).toBe('next');
  let handed;
  if (proposals.length) {
    const reconciled = await g.runner.drive('complete', { chunk_id: chunkId, choice: 'converged', artifacts: [delta] });
    expect(reconciled.body.response?.outcome).toBe('next');
    const docket = { name: 'docket', kind: 'asset', content: JSON.stringify(proposals), attached: true };
    handed = await g.runner.drive('complete', { chunk_id: chunkId, choice: 'proposed', artifacts: [docket] });
  } else {
    handed = await g.runner.drive('complete', { chunk_id: chunkId, choice: 'nothing-to-propose', artifacts: [delta] });
  }
  expect(handed.body.response?.outcome).toBe('hub_node_taken');
  await expect
    .poll(async () => (await g.api.get(`/api/chunks/${chunkId}`)).body.current_node_name, { timeout: 60_000 })
    .not.toBe('deliver');
  const detail = await g.api.get(`/api/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  const exit = (detail.body.history as { from_node_name: string; choice_name: string | null }[])
    .filter((transition) => transition.from_node_name === 'deliver')
    .at(-1);
  const failure = (detail.body.artifacts as { name: string; content: string | null }[])
    .filter((artifact) => artifact.name === 'garden-delivery-failure')
    .at(-1);
  return { outcome: exit?.choice_name ?? undefined, detail: failure?.content ?? '' };
}

function add_op(locus: string, introduced?: string) {
  return { op: 'add', class: 'stale-docstring', locus, summary: `weed at ${locus}`, ...(introduced ? { introduced } : {}) };
}

async function live(g: Garden) {
  const response = await g.api.get('/api/findings', { params: { routine, scope } });
  expect(response.status).toBe(200);
  return response.body.findings as { finding_id: string; locus: string; state: string }[];
}

async function seed(g: Garden, count: number, introduced?: string): Promise<string[]> {
  const response = await deliver(g, Array.from({ length: count }, (_, i) => add_op(`src/app.py:${i}`, introduced)));
  expect(response.outcome).toBe('recorded');
  const rows = await live(g);
  expect(rows.length).toBeGreaterThanOrEqual(count);
  return rows.slice(0, count).map((row) => row.finding_id);
}

async function read_back(g: Garden, id: string) {
  const response = await g.api.get(`/api/findings/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

/** A runner node committing a change and handing it to a hub node taking the `land` action. */
function landingGraph(repo: Repository) {
  return {
    name: 'finding-exits-landing', entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${repo.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/answer-the-weeds'], check=True)",
          "(pathlib.Path(repo) / 'WEEDS_ANSWERED.md').write_text('the proposal answered\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: answer the proposal'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/answer-the-weeds'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/answer-the-weeds', '--commit', sha], check=True)",
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

/** Moves `chunkId` onto the landing graph and lands it: the host builds and commits, the hub's `land` action merges. */
async function land(g: Garden, host: RunnerHost, chunkId: string) {
  const graph = await g.api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(landingGraph(g.repository)) } });
  expect(graph.status, JSON.stringify(graph.body)).toBe(201);
  expect((await g.api.patch(`/api/chunks/${chunkId}`, { data: { graph_id: graph.body.graph_id } })).status).toBe(202);
  expect((await g.api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
  const read = async () => (await g.api.get<Wire.ChunkDetail>(`/api/chunks/${chunkId}`)).body;
  await stepUntil(host, read, (s) => s.landed || s.pending !== null || s.current_node_name === 'deliver', { progress: chunkProgress });
  await expect.poll(async () => (await read()).landed, { timeout: 60_000 }).toBe(true);
}

test.describe('delivering a proposal', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('test_delivering_an_accepted_proposals_minted_item_closes_its_findings_to_delivered', async ({ api, forge, repository, mockRunner, runnerHosts }) => {
    test.slow();
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [first, second] = await seed(g, 2) as [string, string];
    const proposed = await deliver(g, [{ op: 'observed', id: first }, { op: 'observed', id: second }], { proposals: [
      { ref: 'P1', class: 'handoff', title: 'answer both weeds', body: 'one response covering both findings', findings: [first, second] },
    ] });
    expect(proposed.outcome).toBe('recorded');
    const listed = await api.get('/api/garden-proposals');
    expect(listed.body.proposals).toHaveLength(1);
    const proposal = listed.body.proposals[0];
    expect(proposal.findings).toEqual([first, second]);
    const accepted = await api.post(`/api/garden-proposals/${proposal.proposal_id}/accept`, { data: { reason: 'taking it' } });
    expect(accepted.status).toBe(200);
    expect(accepted.body.closure.item_outcome).toBe('minted');
    const pointer = `${accepted.body.closure.source}:${accepted.body.closure.ref}`;
    expect((await read_back(g, first)).state).toBe('live');
    await land(g, await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } }), accepted.body.chunk_id);
    await api.sweep('close_drain');
    for (const id of [first, second]) expect(await read_back(g, id)).toMatchObject({ state: 'delivered', note: `delivered by ${pointer}` });
  });

  test('test_an_operator_proposal_over_two_routines_and_scopes_delivers_its_findings', async ({ api, forge, repository, mockRunner, runnerHosts, project }) => {
    test.slow();
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    expect(commit.status).toBe(200);
    const g = await garden(api, await mockRunner('garden'), repo, commit.body.sha);
    const [first] = await seed(g, 1) as [string];
    const secondScope = 'garden-svc-2';
    const secondRoutine = 'garden-service-2';
    expect((await api.post('/api/scopes', { data: { slug: secondScope, description: '' } })).status).toBe(201);
    const created = await api.post('/api/routines', { data: {
      name: secondRoutine, graph_name: 'garden-routine', default_scope_slug: secondScope, default_model: [], default_effort: null,
    } });
    expect(created.status).toBe(201);
    const recorded = await deliver(g, [add_op('src/other.py:1')], { scope: secondScope, routineId: created.body.routine_id });
    expect(recorded.outcome).toBe('recorded');
    const secondLive = await api.get('/api/findings', { params: { routine: secondRoutine, scope: secondScope } });
    expect(secondLive.status).toBe(200);
    const second = secondLive.body.findings[0].finding_id;
    const authored = await api.post(`/api/projects/${project.slug}/garden-proposals`, { data: {
      title: 'answer both weeds', class: 'handoff', body: 'one response covering both findings', findings: [first, second],
    } });
    expect(authored.status).toBe(201);
    const proposal = authored.body;
    expect(proposal).toMatchObject({ origin: 'operator', routine_name: null });
    expect(proposal.findings.sort()).toEqual([first, second].sort());
    const accepted = await api.post(`/api/garden-proposals/${proposal.proposal_id}/accept`, { data: { reason: 'taking it' } });
    expect(accepted.status).toBe(200);
    expect(accepted.body.closure.item_outcome).toBe('minted');
    const pointer = `${accepted.body.closure.source}:${accepted.body.closure.ref}`;
    expect((await read_back(g, first)).state).toBe('live');
    await land(g, await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } }), accepted.body.chunk_id);
    await api.sweep('close_drain');
    for (const id of [first, second]) expect(await read_back(g, id)).toMatchObject({ state: 'delivered', note: `delivered by ${pointer}` });
  });
});
