import { assertSkeleton, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

function graph(repo: Repository, name = 'default-delivery', pollTimeout = 600) {
  return {
    name, entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${repo.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
          "(pathlib.Path(repo) / 'PR_CI_LANDED.md').write_text('landed via the PR+CI policy\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change the PR+CI policy lands once CI goes green'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/scripted-build'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/scripted-build', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed the change; checks are green')\n", choices: { pass: { description: 'Committed and green.', to: 'deliver' } } },
        retries: { max: 2, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: pollTimeout, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check failed.', to: 'build' },
      } } },
    },
  };
}

async function createGraph(api: HubApi, repo: Repository, name = 'default-delivery', pollTimeout = 600) {
  const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph(repo, name, pollTimeout)) } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  return created.body.graph_id as string;
}

async function lever(forge: ForgeApi, repo: Repository, name: string, method: 'POST' | 'DELETE' = 'POST') {
  const fullName = `${repo.owner}/${repo.name}`;
  const result = method === 'POST'
    ? await forge.call('POST', `/_levers/${name}`, { repo: fullName })
    : await forge.call('DELETE', `/_levers/${name}?repo=${encodeURIComponent(fullName)}`);
  expect(result.status, JSON.stringify(result.body)).toBe(200);
}

type Pull = { number: number; html_url: string; merged: boolean; head: { sha: string; ref: string } };
async function pulls(forge: ForgeApi, repo: Repository): Promise<Pull[]> {
  const result = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`);
  expect(result.status).toBe(200);
  return result.body as Pull[];
}

async function drive(host: RunnerHost, api: HubApi, id: string, reached: (state: Wire.ChunkDetail) => boolean) {
  return stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body, reached);
}

async function nextPoll(api: HubApi, id: string, previous: string) {
  await expect.poll(async () => {
    const detail = (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
    return detail.pending?.next_poll_at !== undefined && detail.pending.next_poll_at !== previous;
  }, { timeout: 30000 }).toBe(true);
  return (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
}

const timeoutTitle = 'test_pr_ci_bounces_a_poll_timeout_back_to_build';

test.describe('delivery policy', () => {
test.use({ isolation: 'fresh-tenant' });

test('test_pr_ci_pends_on_blocked_then_lands_when_green', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  expect(pending.status).toBe('delivering');
  expect(pending.landed).toBe(false);
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
  const reference = { repo: `${repo.owner}/${repo.name}`, number: opened[0]!.number, url: opened[0]!.html_url };
  expect(pending.open_prs).toEqual([reference]);
  expect(pending.awaiting_external_merge).toBe(false);
  expect(pending.landed_repos).toEqual([]);
  expect((await api.get('/api/chunks')).body.chunks.find((s: { chunk_id: string }) => s.chunk_id === chunk.chunk_id).open_prs).toEqual([reference]);
  expect((await forge.call('GET', new URL(reference.url).pathname)).status).toBe(200);
  const findings = pending.artifacts.filter((a) => a.name === 'delivery-findings');
  expect(findings).toHaveLength(1);
  const content = findings[0]!.content || '';
  expect(content).toContain(repo.name);
  expect(content.toLowerCase()).toContain('ci');
  const second = await nextPoll(api, chunk.chunk_id, pending.pending!.next_poll_at);
  const third = await nextPoll(api, chunk.chunk_id, second.pending!.next_poll_at);
  for (const detail of [second, third]) {
    const rows = detail.artifacts.filter((a) => a.name === 'delivery-findings');
    expect(rows).toHaveLength(1);
    expect(rows[0]!.content || '').toBe(content);
  }
  await lever(forge, repo, 'checks_pending', 'DELETE');
  const done = await drive(host, api, chunk.chunk_id, (s) => ['done', 'needs_human'].includes(s.status));
  expect(done.status).toBe('done');
  expect(done.landed).toBe(true);
  expect(done.landed_repos).toHaveLength(1);
  expect(done.landed_repos[0]!.repo).toBe(reference.repo);
  expect(done.open_prs).toEqual([]);
  expect(done.closed_prs).toEqual([reference]);
  expect((await forge.call('GET', new URL(done.landed_repos[0]!.url!).pathname)).status).toBe(200);
  expect((await api.get('/api/chunks')).body.chunks.find((s: { chunk_id: string }) => s.chunk_id === chunk.chunk_id).landed_repos).toEqual(done.landed_repos);
  const after = await host.originHead(repo.name, 'main');
  expect(after).not.toBe(before);
  const history = await host.originLog(repo.name, 'main');
  expect(history![0]!.sha).toBe(after);
  expect(history![0]!.parents).toEqual([before, opened[0]!.head.sha]);
  expect(done.landed_repos[0]!.commit_hash).toBe(after);
  expect(await host.originLog(repo.name, 'main', 'PR_CI_LANDED.md')).toHaveLength(1);
});

test.describe('failure in two owned worlds', () => {
  test.use({ isolation: 'fresh-hub' });
  test('test_pr_ci_routes_failure_on_a_terminally_failed_check', async ({ repository, issue, api, forge, runnerHosts }) => {
    test.slow();
    const first = await repository();
    const second = await repository();
    const origins = { [first.name]: first.origin!, [second.name]: second.origin! };
    const firstHost = await runnerHosts.create({ runnerRepositories: origins });
    const mainBefore = await firstHost.originHead(first.name, 'main');
    await lever(forge, first, 'checks_failed');
    await createGraph(api, first);
    const { chunk } = await issue('pr-ci', { repository: first });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const bounced = await drive(firstHost, api, chunk.chunk_id, (s) => s.bounces?.length > 0);
    expect(bounced.bounces[0]!.cause).toBe('failure');
    expect(bounced.landed).toBe(false);
    const opened = await pulls(forge, first);
    expect(opened.length).toBeGreaterThan(0);
    expect(opened.some((pull) => pull.merged)).toBe(false);
    const findings = bounced.artifacts.find((a) => a.name === 'delivery-findings');
    expect(findings).toBeDefined();
    expect(findings!.content).toContain(first.name);
    expect(findings!.content).toContain('failure');
    expect(findings!.content).not.toContain('not this change');
    expect(await firstHost.originHead(first.name, 'main')).toBe(mainBefore);

    const secondHost = await runnerHosts.create({ runnerRepositories: origins });
    await lever(forge, second, 'checks_failed');
    await lever(forge, second, 'base_checks_failed');
    const secondGraph = await createGraph(api, second, 'second-delivery');
    const secondChunk = (await issue('pr-ci second pass', { repository: second })).chunk;
    expect((await api.patch(`/api/chunks/${secondChunk.chunk_id}`, { data: { graph_id: secondGraph } })).status).toBe(202);
    expect((await api.post(`/api/chunks/${secondChunk.chunk_id}/promote`)).status).toBe(202);
    const inherited = await drive(secondHost, api, secondChunk.chunk_id, (s) => s.bounces?.length > 0);
    expect(inherited.bounces[0]!.cause).toBe('inherited-failure');
    const inheritedFindings = inherited.artifacts.find((a) => a.name === 'delivery-findings');
    expect(inheritedFindings).toBeDefined();
    expect(inheritedFindings!.content).toContain('not this change');
  });
});

test('test_pr_ci_self_heals_a_behind_branch_and_lands', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originCommit(repo.name, 'main', 'BASE_MOVED.md', 'BASE_MOVED.md\n', 'add BASE_MOVED.md');
  await lever(forge, repo, 'stale_branch');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  expect(pending.landed).toBe(false);
  const done = await drive(host, api, chunk.chunk_id, (s) => ['done', 'needs_human'].includes(s.status));
  expect(done.status).toBe('done');
  expect(done.landed).toBe(true);
  expect((await pulls(forge, repo)).filter((pull) => pull.merged)).toHaveLength(1);
  expect(await host.originHead(repo.name, 'main')).not.toBe(before);
  const merge = (await host.originLog(repo.name, 'main'))![0]!;
  expect(merge.parents).toHaveLength(2);
  expect(merge.parents[0]).toBe(before);
  const head = (await host.originLog(repo.name, merge.parents[1]!))![0]!;
  expect(head.parents).toContain(before);
});

test('test_pr_ci_bounces_a_dirty_conflict_back_to_build', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const mainBefore = await host.originHead(repo.name, 'main');
  const fullName = `${repo.owner}/${repo.name}`;
  await lever(forge, repo, 'merge_conflict');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const bounced = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (state) => state.bounces?.length > 0);
  expect(bounced.bounces[0].cause).toBe('conflict');
  expect(bounced.landed).toBe(false);
  const pulls = await forge.call('GET', `/repos/${fullName}/pulls?state=all`);
  expect(pulls.status).toBe(200);
  expect((pulls.body as { merged: boolean }[]).length).toBeGreaterThan(0);
  expect((pulls.body as { merged: boolean }[]).some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(mainBefore);
});

test('test_pr_ci_refuses_a_foreign_commit_on_the_head', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const opened = await pulls(forge, repo);
  expect(opened).toHaveLength(1);
  const foreign = await host.originCommit(repo.name, opened[0]!.head.ref, 'FOREIGN.md', 'FOREIGN.md\n', 'add FOREIGN.md');
  expect(pending.landed).toBe(false);
  await lever(forge, repo, 'checks_pending', 'DELETE');
  const bounced = await drive(host, api, chunk.chunk_id, (s) => s.bounces.length > 0);
  expect(bounced.bounces[0]!.cause).toBe('failure');
  expect(bounced.landed).toBe(false);
  const after = await pulls(forge, repo);
  expect(after.length).toBeGreaterThan(0);
  expect(after.some((pull) => pull.merged)).toBe(false);
  const findings = bounced.artifacts.find((a) => a.name === 'delivery-findings/foreign-head');
  expect(findings).toBeDefined();
  expect(findings!.content).toContain(foreign);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
});

test(timeoutTitle, async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo, 'default-delivery', 4);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  await expect.poll(async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body.bounces.length, { timeout: 30000 }).toBeGreaterThan(0);
  const bounced = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
  expect(bounced.bounces[0]!.cause).toBe('poll-timeout');
  expect(bounced.landed).toBe(false);
  expect(bounced.current_node_name).toBe('build');
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
});

test('test_pr_ci_lands_an_unstable_pr_once_checks_are_green', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const opened = await pulls(forge, repo);
  expect(opened).toHaveLength(1);
  const forced = await forge.call('POST', '/_levers/mergeable_state', { repo: `${repo.owner}/${repo.name}`, number: opened[0]!.number, state: 'unstable' });
  expect(forced.status, JSON.stringify(forced.body)).toBe(200);
  await lever(forge, repo, 'checks_pending', 'DELETE');
  const done = await drive(host, api, chunk.chunk_id, (s) => ['done', 'needs_human'].includes(s.status));
  expect(done.status).toBe('done');
  expect(done.landed).toBe(true);
  const result = await pulls(forge, repo);
  expect(result).toHaveLength(1);
  expect(result[0]!.merged).toBe(true);
  expect(await host.originHead(repo.name, 'main')).not.toBe(before);
});

for (const state of ['draft', 'has_hooks'] as const) {
  test(`test_pr_ci_waits_on_a_draft_or_hooked_pr[${state}]`, async ({ repository, issue, api, forge, runnerHosts }) => {
    test.slow();
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    const before = await host.originHead(repo.name, 'main');
    await lever(forge, repo, 'checks_pending');
    await createGraph(api, repo);
    const { chunk } = await issue('pr-ci', { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
    const opened = await pulls(forge, repo);
    expect(opened).toHaveLength(1);
    const forced = await forge.call('POST', '/_levers/mergeable_state', { repo: `${repo.owner}/${repo.name}`, number: opened[0]!.number, state });
    expect(forced.status, JSON.stringify(forced.body)).toBe(200);
    await lever(forge, repo, 'checks_pending', 'DELETE');
    let previous = pending.pending!.next_poll_at;
    for (let i = 0; i < 3; i++) {
      await expect.poll(async () => {
        const detail = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
        return detail.status !== 'delivering' || (detail.pending !== null && detail.pending.next_poll_at !== previous);
      }, { timeout: 30000 }).toBe(true);
      const later = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
      expect(later.status).toBe('delivering');
      expect(later.landed).toBe(false);
      expect(later.bounces).toEqual([]);
      previous = later.pending!.next_poll_at;
    }
    const result = await pulls(forge, repo);
    expect(result.length).toBeGreaterThan(0);
    expect(result.some((pull) => pull.merged)).toBe(false);
    expect(await host.originHead(repo.name, 'main')).toBe(before);
  });
}

test('test_pr_ci_waits_on_a_startup_failure_check_then_lands', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_startup_failure');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  await expect.poll(async () => {
    const detail = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
    return detail.bounces.length > 0 || (detail.pending !== null && detail.pending.next_poll_at !== pending.pending!.next_poll_at);
  }, { timeout: 30000 }).toBe(true);
  const again = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
  expect(again.bounces).toEqual([]);
  expect(again.landed).toBe(false);
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  await lever(forge, repo, 'checks_startup_failure', 'DELETE');
  const done = await drive(host, api, chunk.chunk_id, (s) => ['done', 'needs_human'].includes(s.status));
  expect(done.status).toBe('done');
  expect(done.bounces).toEqual([]);
  expect(await host.originHead(repo.name, 'main')).not.toBe(before);
});

test('test_pr_ci_routes_conflict_when_updating_a_behind_branch_conflicts', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const before = await host.originCommit(repo.name, 'main', 'PR_CI_LANDED.md', 'PR_CI_LANDED.md\n', 'add PR_CI_LANDED.md');
  await lever(forge, repo, 'stale_branch');
  const bounced = await drive(host, api, chunk.chunk_id, (s) => s.bounces.length > 0);
  expect(bounced.bounces[0]!.cause).toBe('conflict');
  expect(bounced.landed).toBe(false);
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
});

test('test_pr_ci_escalates_a_rejected_credential_as_forge_auth', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  await lever(forge, repo, 'token_rejected');
  const escalated = await drive(host, api, chunk.chunk_id, (s) => s.status === 'needs_human');
  expect(escalated.escalation).not.toBeNull();
  expect(escalated.escalation!.cause).toBe('forge-auth');
  expect(escalated.escalation!.detail || '').toContain(`${repo.owner}/${repo.name}`);
  expect(escalated.landed).toBe(false);
  expect(escalated.current_node_name).toBe('deliver');
  await lever(forge, repo, 'token_rejected', 'DELETE');
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
});

test('test_pr_ci_escalates_a_repository_the_forge_does_not_know_as_forge_config', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const before = await host.originHead(repo.name, 'main');
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const nowhere = `${forge.url}/no-such-api`;
  const moved = await api.patch(`/api/repositories/${repo.name}`, { data: { forge_api_url: nowhere } });
  expect(moved.status, JSON.stringify(moved.body)).toBe(200);
  const escalated = await drive(host, api, chunk.chunk_id, (s) => s.status === 'needs_human');
  expect(escalated.escalation).not.toBeNull();
  expect(escalated.escalation!.cause).toBe('forge-config');
  expect(escalated.escalation!.detail || '').toContain(nowhere);
  expect(escalated.landed).toBe(false);
  const opened = await pulls(forge, repo);
  expect(opened.length).toBeGreaterThan(0);
  expect(opened.some((pull) => pull.merged)).toBe(false);
  expect(await host.originHead(repo.name, 'main')).toBe(before);
});

test('test_pr_ci_does_not_merge_ahead_of_a_check_that_starts_after_the_others_finish', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const pending = await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const opened = await pulls(forge, repo);
  expect(opened).toHaveLength(1);
  const ref = opened[0]!.head.ref;
  const armed = await forge.call('POST', '/_levers/ref_checks', { repo: `${repo.owner}/${repo.name}`, ref, checks: [{ name: 'first', status: 'success' }, { name: 'second', status: 'pending' }] });
  expect(armed.status, JSON.stringify(armed.body)).toBe(200);
  await lever(forge, repo, 'checks_pending', 'DELETE');
  const first = await nextPoll(api, chunk.chunk_id, pending.pending!.next_poll_at);
  await nextPoll(api, chunk.chunk_id, first.pending!.next_poll_at);
  const stillOpen = await pulls(forge, repo);
  expect(stillOpen.length).toBeGreaterThan(0);
  expect(stillOpen.some((pull) => pull.merged)).toBe(false);
  const green = await forge.call('POST', '/_levers/ref_checks', { repo: `${repo.owner}/${repo.name}`, ref, checks: [{ name: 'second', status: 'success' }] });
  expect(green.status, JSON.stringify(green.body)).toBe(200);
  const done = await drive(host, api, chunk.chunk_id, (s) => ['done', 'needs_human'].includes(s.status));
  expect(done.status).toBe('done');
});

test('test_pr_ci_bounces_a_revision_with_no_check_at_its_poll_timeout', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo, 'default-delivery', 4);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  const opened = await pulls(forge, repo);
  expect(opened).toHaveLength(1);
  const armed = await forge.call('POST', '/_levers/ref_checks', { repo: `${repo.owner}/${repo.name}`, ref: opened[0]!.head.ref, checks: [] });
  expect(armed.status, JSON.stringify(armed.body)).toBe(200);
  await lever(forge, repo, 'checks_pending', 'DELETE');
  await expect.poll(async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body.bounces.length, { timeout: 30000 }).toBeGreaterThan(0);
  const bounced = (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body;
  expect(bounced.bounces[0]!.cause).toBe('poll-timeout');
  const result = await pulls(forge, repo);
  expect(result.length).toBeGreaterThan(0);
  expect(result.some((pull) => pull.merged)).toBe(false);
});
});

traced(timeoutTitle, async ({ repository, issue, api, forge, runnerHosts, traceSink }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  await lever(forge, repo, 'checks_pending');
  await createGraph(api, repo, 'default-delivery', 4);
  const { chunk } = await issue('pr-ci', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  await drive(host, api, chunk.chunk_id, (s) => s.pending !== null);
  await expect.poll(async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${chunk.chunk_id}`)).body.bounces.length, { timeout: 30000 }).toBeGreaterThan(0);
  await drive(host, api, chunk.chunk_id, (s) => s.history.filter((h) => h.choice_name === 'pass').length >= 2);
  const chunkSpans = () => traceSink.spans().filter(isFleet).filter((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id);
  await expect.poll(() => {
    const spans = chunkSpans();
    return [spans.filter((span) => roleOf(span) === 'step').length >= 3, spans.some((span) => roleOf(span) === 'runner/invocation')];
  }, { timeout: 30000 }).toEqual([true, true]);
  const traces = tracesOf(chunkSpans());
  const bounce = traces[1]!.root.events.filter((event) => event.name === 'bounce');
  expect(bounce).toHaveLength(1);
  expect(bounce[0]!.attributes['blizzard.bounce.cause']).toBe('poll-timeout');
  assertSkeleton(traces, [
    { name: 'step build', outcome: 'transitioned', toNode: 'deliver', children: ['queue wait', 'claim'] },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'build', events: ['bounce'], minEvents: { 'hub poll pending': 2 }, minChildren: { 'hub exec': 1 }, link: 'next', attributes: { 'blizzard.bounce.cause': 'poll-timeout' } },
    { name: 'step build', outcome: 'transitioned', toNode: 'deliver', link: 'bounce' },
  ]);
});
