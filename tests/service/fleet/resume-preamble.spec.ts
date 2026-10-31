import { expect, stepUntil, test, type HubApi, type Repository, type RunnerHosts } from '../../harness/fixtures';

const RUNNER_PROMPT = [
  'LAYER-ONE-BLIZZARD-FRAMING-SENTINEL',
  'You are a worker in a blizzard fleet: an autonomous fleet-management system that claims',
  'units of work off a queue and drives each through a graph of nodes. You are one step in',
  'that graph, and the runner that spawned you holds your lease. Your interface to the fleet',
  'is the blizzard CLI, already on your PATH. The operator verbs are not yours to run.',
].join('\n');
const WORKSPACE_PROMPT = [
  'LAYER-TWO-WORKSPACE-POLICY-SENTINEL',
  'Work only inside your assigned feature environment worktrees, named in the facts table',
  'below. Before a fresh build, reset to a known-good baseline: bring services down, check',
  'every worktree out to the base branch, destroy and re-provision resources, and only then',
  'start services. Never exercise an environment you have not provisioned. If checkout fails,',
  'stop immediately and report rather than hand-repairing.',
].join('\n');
const REPLACEMENT_PROMPT = [
  'LAYER-TWO-REPLACED-POLICY-SENTINEL',
  'The delivery policy has changed mid-chunk: do not open pull requests directly. Push your',
  'branch and declare it, then let the fleet deliver node open and land the PR on your',
  'behalf. Everything else in the previous policy still stands unless restated here.',
].join('\n');

function graph(repo: string): string {
  const build = [
    'import os, pathlib, subprocess',
    `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${repo}')`,
    "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/resume-preamble'], check=True)",
    "p = pathlib.Path(repo, 'BUILD.md')",
    "p.write_text((p.read_text() if p.exists() else '') + 'build pass\\n')",
    "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
    "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a build pass'], check=True)",
    "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
    `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', '${repo}', '--branch', branch, '--commit', commit], check=True)`,
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: build, session: 'resume:build',
      judgement: { prompt: "verdict('pass', 'checks are green')\n", choices: {
        pass: { description: 'Committed and green.', to: 'review' },
      } }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: 'pass\n', session: 'fresh', produces: ['review-findings'],
      judgement: { prompt: [
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
  } });
}

test.use({ isolation: 'fresh-tenant' });

async function scenario(fixtures: {
  api: HubApi;
  issue: (title: string, options?: { repository?: Repository }) => Promise<{ chunk: { chunk_id: string } }>;
  repository: (name?: string) => Promise<Repository>;
  runnerHosts: RunnerHosts;
}, replace: boolean): Promise<{ first: string; second: string }> {
  const { api, issue, repository, runnerHosts } = fixtures;
  const target = await repository();
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(target.name) } })).status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: {
    workspace: [{ id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main', prompt: WORKSPACE_PROMPT,
      repo: [{ name: target.name, url: target.origin }] }],
    runner: [{ name: 'runner-local', runner_prompt: RUNNER_PROMPT }],
  } });
  const runner = host.runners['runner-local']!;
  const workspaces = await host.api.workspaces();
  expect(workspaces.status).toBe(200);
  expect(workspaces.body.items).toHaveLength(1);
  const workspaceId = workspaces.body.items[0]!.workspace_id;
  expect((await host.api.workspacePrompt(workspaceId)).body).toMatchObject({ prompt: WORKSPACE_PROMPT, source: 'config' });
  const { chunk } = await issue('resume preamble', { repository: target });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);

  if (replace) {
    const reviews = await stepUntil(host, async () => (await runner.leasesForChunk(id)).body.items,
      (leases) => leases.some((lease) => lease.node_name === 'review'), 80);
    expect(reviews.filter((lease) => lease.node_name === 'build')).toHaveLength(1);
    expect((await host.api.replaceWorkspacePrompt(workspaceId, { prompt: REPLACEMENT_PROMPT })).body)
      .toMatchObject({ prompt: REPLACEMENT_PROMPT, source: 'override' });
  }
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
    (body) => body.status === 'done' || body.status === 'needs_human', 80);
  expect(detail.status).toBe('done');
  expect(detail.landed).toBe(true);

  const leasesResponse = await runner.leasesForChunk(id);
  expect(leasesResponse.status).toBe(200);
  const builds = leasesResponse.body.items.filter((lease) => lease.node_name === 'build');
  expect(builds).toHaveLength(2);
  expect(builds[0]!.session_id).toEqual(expect.any(String));
  expect(builds[1]!.session_id).toBe(builds[0]!.session_id);
  const transcript = await runner.leaseTranscript(builds[1]!.lease_id);
  expect(transcript.status).toBe(200);
  expect(transcript.body.available).toBe(true);
  const spawns = transcript.body.turns.filter((turn) => turn.kind === 'env' && turn.text?.includes('| Field | Value |')).map((turn) => turn.text!);
  expect(spawns).toHaveLength(2);
  expect(spawns[0]).toContain(`| lease id | \`${builds[0]!.lease_id}\` |`);
  expect(spawns[1]).toContain(`| lease id | \`${builds[1]!.lease_id}\` |`);
  expect(spawns[1]).not.toContain(builds[0]!.lease_id);
  return { first: spawns[0]!, second: spawns[1]! };
}

test('test_resumed_node_entry_elides_unchanged_standing_layers', async ({ api, issue, repository, runnerHosts }) => {
  test.slow();
  const { first, second } = await scenario({ api, issue, repository, runnerHosts }, false);
  expect(first).toContain(RUNNER_PROMPT);
  expect(first).toContain(WORKSPACE_PROMPT);
  expect(first).not.toContain('standing instructions are unchanged since your previous turn');
  expect(first).not.toContain('standing instructions have been updated');
  expect(second).toContain('standing instructions are unchanged since your previous turn');
  expect(second).not.toContain(RUNNER_PROMPT);
  expect(second).not.toContain(WORKSPACE_PROMPT);
  expect(second).not.toContain('standing instructions have been updated');
  expect(second.length).toBeLessThan(first.length);
});

test('test_resumed_node_entry_announces_a_replaced_workspace_prompt', async ({ api, issue, repository, runnerHosts }) => {
  test.slow();
  const { first, second } = await scenario({ api, issue, repository, runnerHosts }, true);
  expect(first).toContain(WORKSPACE_PROMPT);
  expect(second).toMatch(/^# Your standing instructions have been updated/);
  expect(second).toContain(REPLACEMENT_PROMPT);
  expect(second).not.toContain(WORKSPACE_PROMPT);
  expect(second).toContain('blizzard fleet-worker preamble is unchanged since your previous turn');
  expect(second).not.toContain(RUNNER_PROMPT);
  expect(second).toContain('| Field | Value |');
});
