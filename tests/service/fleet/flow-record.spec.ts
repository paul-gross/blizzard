import { expect, stepUntil, test } from '../../harness/fixtures';

const LANDED = 'HELD.md';
const build = (repoName: string): string => [
  'import os, pathlib, subprocess',
  `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${repoName}')`,
  "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/held'], check=True)",
  `pathlib.Path(repo, '${LANDED}').write_text('held on a flow\\n')`,
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: HELD.md'], check=True)",
  "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
  "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
  `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', '${repoName}', '--branch', branch, '--commit', commit], check=True)`,
].join('\n');

const graph = (repoName: string): string => JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
  build: {
    executor: 'runner', prompt: build(repoName), judgement: {
      prompt: "verdict('pass', 'the mock harness did its turn')\n",
      choices: { pass: { description: 'Built.', to: 'deliver' } },
    }, retries: { max: 1, exhausted: 'escalate' },
  },
  deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
    landed: { description: 'Every repo merged.', to: 'done' },
    conflict: { description: 'Conflict.', to: 'build' },
    failure: { description: 'Failure.', to: 'build' },
    'inherited-failure': { description: 'Base red.', to: 'build' },
  } } },
} });

interface Submission {
  submission_id: string;
  closure: null | { outcome: string };
}

test.use({ isolation: 'fresh-tenant' });

test('test_a_held_chunk_is_submitted_and_its_landing_supersedes_it', async ({ api, forge, issue, repository, runnerHosts }) => {
  test.slow();
  const target = await repository();
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(target.name) } })).status).toBe(201);
  const repo = `${target.owner}/${target.name}`;
  expect((await forge.call('POST', '/_levers/checks_pending', { repo })).status).toBe(200);
  const runnerHost = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: target.name, url: target.origin }],
  }] } });
  const { chunk } = await issue('held flow', { repository: target });
  const chunkId = chunk.chunk_id;
  const submissions = async (): Promise<Submission[]> => {
    const response = await api.get<{ chunk_id: string; items: Submission[] }>(`/api/chunks/${chunkId}/flow-submissions`);
    expect(response.status).toBe(200);
    expect(response.body.chunk_id).toBe(chunkId);
    return response.body.items;
  };
  expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);

  const held = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body,
    (body) => body.pending !== null, 80);
  expect(held.pending).not.toBeNull();
  await expect.poll(submissions).toEqual([expect.objectContaining({ submission_id: expect.any(String), closure: null })]);
  const [open] = await submissions();

  expect((await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(repo)}`)).status).toBe(200);
  const done = await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body,
    (body) => body.status === 'done' || body.status === 'needs_human', 80);
  expect([done.status, done.landed]).toEqual(['done', true]);
  await expect.poll(submissions).toEqual([expect.objectContaining({
    submission_id: open!.submission_id, closure: expect.objectContaining({ outcome: 'landed' }),
  })]);
});
