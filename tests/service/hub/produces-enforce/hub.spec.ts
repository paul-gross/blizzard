import { expect, test } from '../../../harness/fixtures';

// The produces backstop, enforced, driven by the mock runner's `complete` against the real hub. Each test posts a graph
// and files its own runner, so each owns its tenant.
test.use({ isolation: 'fresh-tenant' });

const TAIL = {
  review: {
    executor: 'runner',
    prompt: '# review',
    session: 'fresh',
    judgement: { prompt: '# judge', choices: { pass: { description: 'clean', to: 'deliver' } } },
    retries: { max: 1, exhausted: 'escalate' },
  },
  deliver: {
    executor: 'hub',
    action: 'pass',
    judgement: {
      choices: { success: { description: 'Delivered.', to: 'done' }, failure: { description: 'Failed to deliver.', to: 'build' } },
    },
  },
};

/** A `default-delivery` graph whose `build` node declares what it must produce; its `pass` advances to `review`. */
const graph = (produces: unknown[]): string =>
  JSON.stringify({
    name: 'default-delivery',
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        prompt: '# build',
        produces,
        judgement: { prompt: '# judge', choices: { pass: { description: 'green', to: 'review' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      ...TAIL,
    },
  });

/** Name-only: the `notes` asset. */
const NOTES_GRAPH = graph(['notes']);
/** By kind: any `git_commit` artifact, whatever its name. */
const COMMIT_KIND_GRAPH = graph([{ name: 'commit', kind: 'git_commit' }]);

const ATTACHED_NOTES = [{ name: 'notes', kind: 'asset', content: 'the real thing', attached: true }];
const FALLBACK_NOTES = [{ name: 'notes', kind: 'asset', content: 'assessment fallback', attached: false }];
// A name covered by a pushed commit rather than an attach: `attached` is absent.
const GIT_COMMIT_NOTES = [
  { name: 'notes', kind: 'git_commit', repo: 'toy-api', branch_name: 'bz/notes', commit_hash: 'a'.repeat(40) },
];
// Named after the repo, never the declared produces name: proves a kind match, not a coincidental name match.
const GIT_COMMIT_REPO_NAMED = [
  { name: 'toy-api', kind: 'git_commit', repo: 'toy-api', branch_name: 'bz/build', commit_hash: 'a'.repeat(40) },
];

test('test_fallback_only_completion_is_rejected_under_enforce_over_the_wire', async ({ api, issue, mockRunner }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: NOTES_GRAPH } })).status).toBe(201);
  const { chunk } = await issue('produces enforce reject');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const runner = await mockRunner('runner-produces-reject');
  await runner.drive('register');
  expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
  const before = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id;

  const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass', artifacts: FALLBACK_NOTES });

  expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('failure');
  expect(out.body.response?.detail ?? '').toContain('notes');
  expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id).toBe(before);
});

for (const [title, definition, artifacts] of [
  ['test_explicit_attachment_is_accepted_under_enforce_over_the_wire', NOTES_GRAPH, ATTACHED_NOTES],
  ['test_git_commit_covered_produces_name_is_accepted_under_enforce_over_the_wire', NOTES_GRAPH, GIT_COMMIT_NOTES],
  ['test_git_commit_kind_expectation_is_accepted_by_kind_not_name_under_enforce_over_the_wire', COMMIT_KIND_GRAPH, GIT_COMMIT_REPO_NAMED],
] as const) {
  test(title, async ({ api, issue, mockRunner }) => {
    expect((await api.post('/api/graphs', { data: { definition_yaml: definition } })).status).toBe(201);
    const { chunk } = await issue(title);
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const runner = await mockRunner('runner-produces-accept');
    await runner.drive('register');
    const claim = await runner.drive('claim', { chunk_id: chunk.chunk_id });
    expect(claim.body.claimed).toBe(true);

    const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass', artifacts });

    expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('next');
    expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id).not.toBe(claim.body.from_node_id);
  });
}

test('test_git_commit_kind_expectation_with_zero_commits_is_rejected_under_enforce_over_the_wire', async ({ api, issue, mockRunner }) => {
  expect((await api.post('/api/graphs', { data: { definition_yaml: COMMIT_KIND_GRAPH } })).status).toBe(201);
  const { chunk } = await issue('produces enforce git-commit kind reject');
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const runner = await mockRunner('runner-produces-kind-reject');
  await runner.drive('register');
  expect((await runner.drive('claim', { chunk_id: chunk.chunk_id })).body.claimed).toBe(true);
  const before = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id;

  // No asset artifact of any name can satisfy a kind expectation.
  const out = await runner.drive('complete', { chunk_id: chunk.chunk_id, choice: 'pass' });

  expect(out.body.response?.outcome, JSON.stringify(out.body)).toBe('failure');
  expect(out.body.response?.detail ?? '').toContain('commit');
  expect((await api.get(`/api/chunks/${chunk.chunk_id}`)).body.current_node_id).toBe(before);
});
