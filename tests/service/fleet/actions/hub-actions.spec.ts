import { actionLeaseGate, actionPause, actionPrompt, actionReadback, breakActionEdge, chunkProgress, expect, stepUntil, test } from '../../../harness/fixtures';

const REPO = 'toy-api';
const LANDED = 'ACTION_LANDED.md';
const BUILD = [
  'import os, pathlib, subprocess',
  "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / 'toy-api')",
  "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
  `pathlib.Path(repo, '${LANDED}').write_text('landed by the land action\\n')`,
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change the land action lands'], check=True)",
  "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
  "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', 'toy-api', '--branch', branch, '--commit', commit], check=True)",
].join('\n');

const TEST_NODES = {
  pick: { executor: 'hub', action: 'test.answer', with: { choice: 'onward' }, judgement: { choices: {
    onward: { description: 'On to walk.', to: 'walk' }, failure: { description: 'Failed.', to: 'build' },
  } } },
  walk: { executor: 'hub', action: 'test.sequence', with: { choices: ['pending', 'done'] }, poll_interval: 1, poll_timeout: 600,
    judgement: { choices: { done: { description: 'Walked.', to: 'done' }, failure: { description: 'Failed.', to: 'build' } } } },
};

function actionGraph(prompt: string, nodes: Record<string, unknown>, first: string): string {
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'the mock harness did its turn')\n", choices: {
      pass: { description: 'Built.', to: first },
    } }, retries: { max: 1, exhausted: 'escalate' } },
    ...nodes,
  } });
}

test.use({ isolation: 'fresh-tenant' });
test.beforeEach(async () => { if (process.env.BLIZZARD_ACTION_WALK === 'manual') test.setTimeout(0); });

test('test_land_lands_a_chunk_against_the_mock_forge', async ({ api, issue, repository, runnerHosts, forge, cli, testHub, tenant, workerCheckpoint }) => {
  test.slow();
  expect((await api.post('/api/graphs', { data: { definition_yaml: actionGraph(actionPrompt(BUILD, workerCheckpoint), { deliver: {
    executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Every repo merged.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base red.', to: 'build' },
    } },
  } }, 'deliver') } })).status).toBe(201);
  const target = await repository(REPO);
  const repoFull = `${target.owner}/${target.name}`;
  const ref = async () => ((await forge.call('GET', `/repos/${repoFull}/git/ref/heads/main`)).body as { object: { sha: string } }).object.sha;
  const before = await ref();
  const runnerHost = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: REPO, url: target.origin }],
  }] } });
  const { chunk } = await issue('actions', { repository: target });
  actionPause(`Promote chunk ${chunk.chunk_id} and step its runner to the next outcome`);
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await actionLeaseGate(runnerHost, chunk.chunk_id, workerCheckpoint, testHub, tenant);
  const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (body) => body.status === 'done', { stallPasses: 80, progress: chunkProgress });
  expect(detail.landed).toBe(true);
  await actionReadback('landed', chunk.chunk_id, api, cli, testHub, tenant);
  actionPause('Inspect the landed readback before the forge comparison');
  const after = await ref();
  expect(after).toBeTruthy();
  expect(after).not.toBe(before);
  const landed = detail.landed_repos as { repo: string; commit_hash: string }[];
  expect(landed).toEqual([expect.objectContaining({ repo: repoFull, commit_hash: after })]);
  expect(detail.open_prs).toEqual([]);
  const pulls = (await forge.call('GET', `/repos/${landed[0]!.repo}/pulls?state=all`)).body as { merged: boolean }[];
  expect(pulls).toHaveLength(1);
  expect(pulls[0]!.merged).toBe(true);
  const comparison = (await forge.call('GET', `/repos/${repoFull}/compare/${before}...${after}`)).body as { files: { filename: string }[]; commits: unknown[] };
  expect(comparison.files.map((file) => file.filename)).toContain(LANDED);
  expect(comparison.commits).toHaveLength(2);
});

test('test_test_actions_drive_hub_nodes', async ({ api, issue, runnerHost, cli, testHub, tenant, workerCheckpoint }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: actionGraph(actionPrompt('pass\n', workerCheckpoint), TEST_NODES, 'pick') } })).status).toBe(201);
  const { chunk } = await issue('test actions');
  actionPause(`Promote chunk ${chunk.chunk_id} and step its runner to the next outcome`);
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await actionLeaseGate(runnerHost, chunk.chunk_id, workerCheckpoint, testHub, tenant);
  const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (body) => body.status === 'done', { stallPasses: 80, progress: chunkProgress });
  await actionReadback('transition', chunk.chunk_id, api, cli, testHub, tenant);
  actionPause('Inspect transition history and turn order');
  expect((detail.history as { choice_name: string }[]).map((item) => item.choice_name).slice(-2)).toEqual(['onward', 'done']);
  expect(Object.fromEntries((detail.artifacts as { name: string; content: string }[]).filter((item) => item.name.startsWith('test-sequence/')).map((item) => [item.name, item.content]))).toEqual({
    'test-sequence/0': 'pending', 'test-sequence/1': 'done',
  });
});

for (const target of ['triage', 'absent'] as const) {
  test(`test_a_hub_exit_routes_to_${target}`, async ({ api, issue, runnerHost, cli, testHub, tenant, workerCheckpoint }) => {
    let destinationId = '';
    if (target === 'triage') {
      const destination = JSON.stringify({ name: 'triage', entry: 'build', nodes: {
        build: { executor: 'runner', prompt: 'Triage.', judgement: { prompt: 'Assess.', choices: {
          pass: { description: 'Done.', to: 'done' },
        } } },
      } });
      const created = await api.post('/api/graphs', { data: { definition_yaml: destination } });
      expect(created.status).toBe(201);
      destinationId = created.body.graph_id;
    }
    const graph = actionGraph(actionPrompt('pass\n', workerCheckpoint), { deliver: { executor: 'hub', action: 'test.answer', with: { choice: 'landed' }, judgement: { choices: {
      landed: { description: 'Landed.', to: `graph:${target}` }, failure: { description: 'Failed.', to: 'build' },
    } } } }, 'deliver');
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
    const { chunk } = await issue(`cross graph ${target}`);
    actionPause(`Promote chunk ${chunk.chunk_id} and step its runner to the next outcome`);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    await actionLeaseGate(runnerHost, chunk.chunk_id, workerCheckpoint, testHub, tenant);
    const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body,
      (body) => target === 'triage' ? body.graph_id === destinationId : body.status === 'needs_human',
      { stallPasses: 80, progress: chunkProgress });
    await actionReadback(`migration-${target}`, chunk.chunk_id, api, cli, testHub, tenant);
    actionPause(`Inspect migration-${target} readback`);
    if (target === 'triage') {
      expect(detail.current_node_name).toBe('build');
    } else {
      expect(detail.escalation?.cause).toBe('migration-target-unresolvable');
      expect((detail.artifacts as { name: string }[]).map((item) => item.name)).not.toContain('hub-unroutable-outcome');
    }
  });
}

test.describe('an invalid stored edge', () => {
  test.use({ isolation: 'fresh-hub' });

  test('test_an_unroutable_hub_outcome_escalates_once', async ({ api, issue, runnerHost, testHub, events, cli, tenant, workerCheckpoint }) => {
    const graph = actionGraph(actionPrompt('pass\n', workerCheckpoint), { deliver: { executor: 'hub', action: 'test.answer', with: { choice: 'landed' }, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, failure: { description: 'Failed.', to: 'build' },
    } } } }, 'deliver');
    const created = await api.post('/api/graphs', { data: { definition_yaml: graph } });
    expect(created.status).toBe(201);
    breakActionEdge(testHub, created.body.graph_id, 'deliver', 'done');
    const { chunk } = await issue('unroutable hub action');
    const subscription = await events.open({ barrier: true });
    actionPause(`Promote chunk ${chunk.chunk_id} and step its runner to the next outcome`);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    await actionLeaseGate(runnerHost, chunk.chunk_id, workerCheckpoint, testHub, tenant);
    const detail = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body,
      (body) => body.status === 'needs_human', { stallPasses: 80, progress: chunkProgress });
    await actionReadback('unroutable', chunk.chunk_id, api, cli, testHub, tenant);
    actionPause('Inspect the unroutable event and escalation readback');
    expect(detail.current_node_name).toBe('deliver');
    expect((detail.artifacts as { name: string }[]).filter((item) => item.name === 'hub-unroutable-outcome')).toHaveLength(1);
    const frames = await subscription.barrier();
    expect(frames.filter((frame) => frame.event === 'event-logged' && frame.data?.chunk_id === chunk.chunk_id && frame.data?.kind === 'hub-node-unroutable-outcome')).toHaveLength(1);
    const logged = (await api.get('/api/events')).body.events.filter((event: { chunk_id: string; kind: string }) => event.chunk_id === chunk.chunk_id && event.kind === 'hub-node-unroutable-outcome');
    expect(logged).toHaveLength(1);
    const feed = (await api.get(`/api/events?chunk_id=${chunk.chunk_id}`)).body.events;
    expect(feed.map((event: { kind: string }) => event.kind).slice(0, 2)).toEqual(['needs-human', 'hub-node-unroutable-outcome']);
    expect(Date.parse(feed[1].recorded_at)).toBeLessThanOrEqual(Date.parse(feed[0].recorded_at));
  });
});

for (const scenario of ['merge_conflict', 'checks_pending'] as const) {
  test(`test_land_${scenario}_walk`, async ({ api, issue, repository, runnerHosts, forge, cli, testHub, tenant, workerCheckpoint }) => {
    if (process.env.BLIZZARD_ACTION_WALK !== 'manual') test.setTimeout(180_000);
    const target = await repository();
    const full = `${target.owner}/${target.name}`;
    const host = await runnerHosts.create({ runnerConfig: { workspace: [{
      id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
      repo: [{ name: target.name, url: target.origin }],
    }] } });
    expect((await forge.call('POST', `/_levers/${scenario}`, { repo: full })).status).toBe(200);
    const created = await api.post('/api/graphs', { data: { definition_yaml: actionGraph(actionPrompt(BUILD.replaceAll(REPO, target.name), workerCheckpoint), { deliver: {
      executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base red.', to: 'build' },
      } },
    } }, 'deliver') } });
    expect(created.status).toBe(201);
    const { chunk } = await issue(scenario, { repository: target });
    console.log(`action-walk ${scenario} graph=${created.body.graph_id} chunk=${chunk.chunk_id} repo=${full}`);
    actionPause(`Promote chunk ${chunk.chunk_id} and step its runner to the next outcome`);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    await actionLeaseGate(host, chunk.chunk_id, workerCheckpoint, testHub, tenant);
    const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body,
      (row) => scenario === 'merge_conflict' ? row.bounces.length > 0 : row.pending !== null,
      { stallPasses: 80, progress: chunkProgress });
    await actionReadback(scenario, chunk.chunk_id, api, cli, testHub, tenant);
    actionPause(`Inspect the ${scenario} readback`);
    if (scenario === 'merge_conflict') {
      expect(detail.current_node_name).toBe('build');
      expect(detail.bounces[0].cause).toBe('conflict');
      expect(detail.history.map((row: { choice_name: string }) => row.choice_name)).toEqual(['pass', 'conflict']);
      expect(detail.artifacts.filter((item: { name: string }) => item.name === 'bounce-envelope')).toHaveLength(1);
      expect(detail.landed).toBe(false);
    } else {
      expect(detail.current_node_name).toBe('deliver');
      expect(detail.status).toBe('delivering');
      const submissions = await api.get(`/api/chunks/${chunk.chunk_id}/flow-submissions`);
      expect(submissions.body.items).toHaveLength(1);
      const submission = submissions.body.items[0];
      const turns = await api.get(`/api/chunks/${chunk.chunk_id}/flow-submissions/${submission.submission_id}/turns`);
      expect(turns.body.items.some((turn: { outcome: string }) => turn.outcome === 'pending')).toBe(true);
      console.log(`action-walk submission=${submission.submission_id} turns=${JSON.stringify(turns.body.items.map((turn: { turn_id: string; outcome: string }) => [turn.turn_id, turn.outcome]))}`);
      actionPause('Release checks_pending and step until landed');
      expect((await forge.call('DELETE', `/_levers/${scenario}?repo=${encodeURIComponent(full)}`)).status).toBe(200);
      const done = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body,
        (row) => row.status === 'done', { stallPasses: 80, progress: chunkProgress });
      expect(done.landed).toBe(true);
      expect(done.history.map((row: { choice_name: string }) => row.choice_name)).toEqual(['pass', 'landed']);
      await actionReadback('checks_released', chunk.chunk_id, api, cli, testHub, tenant);
      actionPause('Inspect the landed turn, event and landing fact');
      const facts = await api.get(`/api/chunks/${chunk.chunk_id}/landing-facts`);
      expect(facts.body.items.map((fact: { repo: string }) => fact.repo)).toEqual([full]);
      const finishedTurns = (await api.get(`/api/chunks/${chunk.chunk_id}/flow-submissions/${submission.submission_id}/turns?limit=200`)).body.items;
      expect(finishedTurns[0].outcome).toBe('landed');
      expect(finishedTurns.slice(1).some((turn: { outcome: string }) => turn.outcome === 'pending')).toBe(true);
    }
  });
}
