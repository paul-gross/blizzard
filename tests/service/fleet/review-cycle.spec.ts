import { assertChunkTrace, assertConforms, assertInvocations, assertRunnerNesting, assertSkeleton, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type RunnerHost, type StepExpect } from '../../harness/fixtures';

const branch = 'feature/scripted-build';
const harnesses = [
  { name: 'claude-code', id: 'claude_code', model: 'claude-opus-4-8' },
  { name: 'opencode', id: 'opencode', model: 'mock-provider/opencode-e2e' },
];

function graph(host: RunnerHost, repo: string, harness: typeof harnesses[number]): string {
  const cli = JSON.stringify(host.cliBinary);
  const declare = `sha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  const build = `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\nfile = repo / 'BUILD.md'\nfile.write_text((file.read_text() if file.exists() else '') + 'build pass\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a build pass'], check=True)\n${declare}`;
  const addendum = `# address the review findings\n(repo / 'REVIEW_ADDRESSED.md').write_text('addressed the review findings\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'fix: address review findings'], check=True)\n${declare}`;
  const review = `import pathlib, subprocess\nmarker = pathlib.Path('.review-count')\nsubprocess.run([${cli}, 'runner', 'artifact', 'create', '--name', 'review-findings'], input='BLOCKING: guard empty input before delivery' if not marker.exists() else 'findings addressed', text=True, check=True)\n`;
  const judgement = `import pathlib\nmarker = pathlib.Path('.review-count')\nvisit = (int(marker.read_text()) if marker.exists() else 0) + 1\nmarker.write_text(str(visit))\nverdict('fail' if visit == 1 else 'pass', 'BLOCKING: guard empty input before delivery' if visit == 1 else 'findings addressed; ready to deliver')\n`;
  const nodes: Record<string, unknown> = {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'checks are green')\n", choices: { pass: { description: 'Committed.', to: 'review' }, fail: { description: 'Incomplete.', to: 'build', prompt_addendum: '# address the findings\n' } } }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: review, session: 'fresh', produces: ['review-findings'], judgement: { prompt: judgement, choices: { pass: { description: 'Passes review.', to: 'deliver' }, fail: { description: 'Blocking issues.', to: 'build', prompt_addendum: addendum } } }, retries: { max: 1, exhausted: 'escalate' } },
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

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'forge' | 'issue' | 'repository' | 'runnerHosts'>;
async function cycle({ api, forge, issue, repository, runnerHosts }: World, harness: typeof harnesses[number]) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: [repo], ...(harness.name === 'opencode' ? { runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] } } : {}) });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, repo.name, harness) } })).status).toBe(201);
  const { chunk } = await issue('review cycle', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  const status = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (value) => ['done', 'needs_human', 'stopped'].includes(value));
  const detail = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  const pulls = (await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`)).body as { merged: boolean }[];
  return { host, repo, chunk, status, detail, pulls };
}

for (const harness of harnesses) {
  test.describe(harness.name, () => {
    test.use({ isolation: 'fresh-tenant' });
    test('test_review_cycle_fails_once_then_delivers', async ({ api, forge, issue, repository, runnerHosts }) => {
      test.setTimeout(120000);
      const state = await cycle({ api, forge, issue, repository, runnerHosts }, harness);
      expect(state.status).toBe('done');
      expect(state.pulls.some((pull) => pull.merged)).toBe(true);
      const history = state.detail.history as { choice_name: string }[];
      expect(history.some((step) => step.choice_name === 'fail')).toBe(true);
      expect(history.some((step) => step.choice_name === 'pass')).toBe(true);
      const findings = (state.detail.artifacts as { name: string; kind: string; content?: string }[]).filter((artifact) => artifact.name === 'review-findings' && artifact.kind === 'asset');
      expect(findings.some((artifact) => artifact.content?.includes('BLOCKING'))).toBe(true);
      expect((await state.host.originFile(state.repo.name, 'main', 'BUILD.md'))?.match(/build pass/g)).toHaveLength(2);
      expect(await state.host.originTree(state.repo.name, 'main')).toContain('REVIEW_ADDRESSED.md');
    });
  });
}

traced('test_review_cycle_fails_once_then_delivers', async ({ api, forge, issue, repository, runnerHosts, traceSink }) => {
  test.setTimeout(150000);
  const state = await cycle({ api, forge, issue, repository, runnerHosts }, harnesses[0]!);
  await expect.poll(() => {
    const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
    return [spans.filter((span) => roleOf(span) === 'step').length, spans.filter((span) => roleOf(span) === 'runner/invocation').length];
  }, { timeout: 30000 }).toEqual([5, 8]);
  const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  assertConforms(spans);
  const traces = tracesOf(spans);
  assertSkeleton(traces, [
    { name: 'step build', outcome: 'transitioned', toNode: 'review', children: ['queue wait', 'claim'] },
    { name: 'step review', outcome: 'transitioned', toNode: 'build', link: 'next' },
    { name: 'step build', outcome: 'transitioned', toNode: 'review', link: 'next' },
    { name: 'step review', outcome: 'transitioned', toNode: 'deliver', link: 'next' },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'done', children: ['hub exec'], link: 'next' },
  ] satisfies StepExpect[]);
  expect(traces.map((trace) => trace.root.attributes['blizzard.step.visit'])).toEqual([1, 1, 2, 2, 1]);
  expect(traces.slice(0, 2).map((trace) => trace.root.attributes['blizzard.step.choice'])).toEqual(['pass', 'fail']);
  const life = assertChunkTrace(spans, state.chunk.chunk_id, { outcome: 'done', steps: 5 }).lifetime;
  const backlog = spans.filter((span) => span.name === 'backlog wait' && span.parentSpanId === life.spanId);
  expect(backlog).toHaveLength(1);
  expect(backlog[0]!.startTimeUnixNano).toBe(life.startTimeUnixNano);
  expect(BigInt(backlog[0]!.endTimeUnixNano)).toBeLessThanOrEqual(BigInt(traces[0]!.root.startTimeUnixNano));
  expect(Math.round(Number(BigInt(backlog[0]!.endTimeUnixNano) - BigInt(backlog[0]!.startTimeUnixNano)) / 1_000_000)).toBe(life.attributes['blizzard.chunk.backlog_ms']);
  assertInvocations(assertRunnerNesting(spans, 4), { build: { harnessId: 'claude_code', responseModel: harnesses[0]!.model }, review: { harnessId: 'claude_code', responseModel: harnesses[0]!.model } });
});
