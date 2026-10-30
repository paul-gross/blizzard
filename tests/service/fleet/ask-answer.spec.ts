import { assertInvocations, assertNoLeaks, assertPlatformNesting, assertRunnerNesting, assertSkeleton, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type StepExpect } from '../../harness/fixtures';

const questionText = 'Which API style should the endpoint use?';
const branch = 'feature/scripted-build';
const harnesses = [
  { name: 'claude-code', id: 'claude_code', model: 'claude-opus-4-8' },
  { name: 'opencode', id: 'opencode', model: 'mock-provider/opencode-e2e' },
];

function graph(harness: typeof harnesses[number], cli: string): string {
  const command = JSON.stringify(cli);
  const build = `import os, subprocess\nsubprocess.run([${command}, 'runner', 'work-items', os.environ['BLIZZARD_CHUNK_ID']], check=True, capture_output=True)\nask(${JSON.stringify(questionText)}, ['rest', 'graphql'])\n`;
  const review = `import subprocess\nsubprocess.run([${command}, 'runner', 'artifact', 'create', '--name', 'review-findings'], input='clean review', text=True, check=True)\n`;
  const nodes: Record<string, unknown> = {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'resumed with the human answer; committed and green')\n", choices: { pass: { description: 'Committed.', to: 'review' } } }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: review, session: 'fresh', produces: ['review-findings'], judgement: { prompt: "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n", choices: { pass: { description: 'Reviewed.', to: 'deliver' }, fail: { description: 'Blocking.', to: 'build' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  };
  const definition: Record<string, unknown> = { name: 'default-delivery', entry: 'build', nodes };
  if (harness.name === 'opencode') {
    definition.sessions = Object.fromEntries(['build', 'review'].map((name) => [`opencode-${name}`, { harnesses: ['opencode'], model: [harness.model] }]));
    (nodes.build as Record<string, unknown>).session = 'resume:opencode-build';
    (nodes.review as Record<string, unknown>).session = 'fresh:opencode-review';
  }
  return JSON.stringify(definition);
}

function answer(repo: string, cli: string): string {
  return `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n(repo / 'LANDED.md').write_text('landed after the human answered\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: resolve the ask and land the change'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${JSON.stringify(cli)}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'cli' | 'forge' | 'issue' | 'repository' | 'runnerHosts'>;
async function scenario({ api, cli, forge, issue, repository, runnerHosts }: World, harness: typeof harnesses[number]) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: [repo], ...(harness.name === 'opencode' ? { runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] } } : {}) });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(harness, host.cliBinary) } })).status).toBe(201);
  const { chunk } = await issue('ask/answer', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  const parked = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => ['waiting_on_human', 'done', 'needs_human'].includes(status));
  const detail = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  const question = (detail.questions as { question_id: string; session_id: string; options: string[] }[])[0]!;
  const before = (await host.mockSessions()).find((record) => record.session_id === question.session_id);
  const passes = [];
  for (let index = 0; index < 4; index++) passes.push(await host.step());
  const still = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  const open = (await api.get('/api/questions')).body as { question_id: string }[];
  const result = await cli(['hub', 'question', 'answer', question.question_id, answer(repo.name, host.cliBinary), '--by', 'alice']);
  const landed = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => ['done', 'needs_human', 'stopped'].includes(status));
  const closed = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.questions as { question_id: string; answered: boolean; delivered: boolean }[];
  const pulls = (await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`)).body as { merged: boolean }[];
  const session = (await host.mockSessions()).find((record) => record.session_id === question.session_id);
  return { host, chunk, repo, parked, detail, question, before, passes, still, open, result, landed, closed, pulls, session };
}

for (const harness of harnesses) {
  test.describe(harness.name, () => {
    test.use({ isolation: 'fresh-tenant' });
    test('test_ask_parks_then_answer_resumes_session_to_done', async ({ api, cli, forge, issue, repository, runnerHosts }) => {
      test.setTimeout(120000);
      const state = await scenario({ api, cli, forge, issue, repository, runnerHosts }, harness);
      expect(state.parked).toBe('waiting_on_human');
      expect(state.question.options).toEqual(['rest', 'graphql']);
      expect(state.passes.map((pass) => pass.outcome)).toEqual(['completed', 'completed', 'completed', 'completed']);
      expect(state.still.status).toBe('waiting_on_human');
      expect(state.open.map((question) => question.question_id)).toEqual([state.question.question_id]);
      expect(state.result.code, state.result.stderr).toBe(0);
      expect(state.landed).toBe('done');
      expect(state.closed.find((question) => question.question_id === state.question.question_id)).toMatchObject({ answered: true, delivered: true });
      expect(state.pulls.some((pull) => pull.merged)).toBe(true);
      expect(state.before).toBeDefined();
      expect(state.session?.session_id).toBe(state.question.session_id);
      expect(state.session?.turns as number).toBeGreaterThan(state.before?.turns as number);
      expect((state.session?.resumes as string[]).some((text) => text.includes('resolve the ask'))).toBe(true);
      expect(await state.host.originTree(state.repo.name, 'main')).toContain('LANDED.md');
    });
  });
}

traced('test_ask_parks_then_answer_resumes_session_to_done', async ({ api, cli, forge, issue, repository, runnerHosts, traceSink, freshHub }) => {
  test.setTimeout(150000);
  const state = await scenario({ api, cli, forge, issue, repository, runnerHosts }, harnesses[0]!);
  const sink = traceSink;
  await expect.poll(() => {
    const spans = sink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
    return [spans.filter((span) => roleOf(span) === 'step').length, spans.filter((span) => roleOf(span) === 'runner/invocation').length];
  }, { timeout: 30000 }).toEqual([3, 5]);
  const spans = sink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  assertSkeleton(tracesOf(spans), [
    { name: 'step build', outcome: 'transitioned', toNode: 'review', children: ['queue wait', 'claim', 'ask'], childAttributes: [{ name: 'ask', key: 'blizzard.ask.answered', value: true }] },
    { name: 'step review', outcome: 'transitioned', toNode: 'deliver', link: 'next' },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'done', children: ['hub exec'], link: 'next' },
  ] satisfies StepExpect[]);
  const runners = assertRunnerNesting(spans, 2);
  expect(runners.some((span) => span.name === 'parked on ask')).toBe(true);
  assertInvocations(runners, { build: { harnessId: 'claude_code', responseModel: harnesses[0]!.model }, review: { harnessId: 'claude_code', responseModel: harnesses[0]!.model } });
  await expect.poll(() => {
    const traceId = tracesOf(spans)[0]!.root.traceId;
    const exported = sink.spans().filter((span) => span.traceId === traceId);
    try { assertPlatformNesting(exported.filter(isFleet), exported.filter((span) => !isFleet(span))); return true; }
    catch { return false; }
  }, { timeout: 30000 }).toBe(true);
  const tokens = (await state.host.mockSessions()).flatMap((session) => {
    const credential = session.spawn_credential as { lease_token?: string } | undefined;
    return credential?.lease_token ? [credential.lease_token] : [];
  });
  await state.host.stop();
  await freshHub.stop();
  expect(sink.spans().some((span) => span.scope.name === 'blizzard.cli' && span.attributes['blizzard.cli.command'] === 'runner artifact create')).toBe(true);
  assertNoLeaks(sink.spans(), { 'lease token': tokens, 'runner bearer': [state.host.hubTokens().runners['runner-local']!], 'ask text': [questionText] });
});
