import { assertChunkTrace, assertConforms, assertIdentity, assertInvocations, assertNoLeaks, assertPlatformNesting, assertRunnerNesting, assertSkeleton, collectorTest, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf, type RunnerHost, type StepExpect } from '../../harness/fixtures';

type Harness = { name: string; id: string; responseModel: string; requestModel?: string };
const harnesses: Harness[] = [
  { name: 'claude-code', id: 'claude_code', responseModel: 'claude-opus-4-8' },
  { name: 'opencode', id: 'opencode', responseModel: 'mock-provider/opencode-e2e', requestModel: 'mock-provider/opencode-e2e' },
];
const branch = 'feature/scripted-build';
const artifactBody = 'SENTINEL-REVIEW-FINDINGS: never exported in a trace';

function graph(host: RunnerHost, harness: Harness, repoName: string, options: { passthrough?: boolean; probed?: boolean; checkpoint?: string } = {}): string {
  const cli = JSON.stringify(host.cliBinary);
  const build = `import os, pathlib, subprocess, json\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repoName)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n` +
    (options.probed ? `pathlib.Path(os.environ['BLIZZARD_TMPDIR'], 'lease-probe').write_text(os.environ['BLIZZARD_LEASE_TOKEN'])\n` : '') +
    (options.passthrough ? `item = json.loads(subprocess.run([${cli}, 'runner', 'work-items', os.environ['BLIZZARD_CHUNK_ID']], capture_output=True, text=True, check=True).stdout)['items'][0]\ntext = item['body'] + '\\n' + '\\n'.join(item['comments']) + '\\n'\n` : options.probed ? `subprocess.run([${cli}, 'runner', 'work-items', os.environ['BLIZZARD_CHUNK_ID']], check=True, capture_output=True)\ntext = 'landed by the mock harness\\n'\n` : `text = 'landed by the mock harness\\n'\n`) +
    (options.checkpoint ?? '') +
    ` (repo / 'LANDED.md').write_text(text)\n`.trimStart() +
    `subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change from the mock harness'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repoName)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  const review = `import subprocess\nsubprocess.run([${cli}, 'runner', 'artifact', 'create', '--name', 'review-findings'], input=${JSON.stringify(artifactBody)}, text=True, check=True)\n`;
  const nodes: Record<string, unknown> = {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'the mock harness committed the change; checks are green')\n", choices: { pass: { description: 'Committed and green.', to: 'review' } } }, retries: { max: 1, exhausted: 'escalate' } },
    review: { executor: 'runner', prompt: review, session: 'fresh', produces: ['review-findings'], judgement: { prompt: "verdict('pass', 'cold-eyes review: ready to deliver')\n", choices: { pass: { description: 'Reviewed.', to: 'deliver' }, fail: { description: 'Blocking.', to: 'build' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  };
  const definition: Record<string, unknown> = { name: 'default-delivery', entry: 'build', nodes };
  if (harness.name === 'opencode') {
    definition.sessions = Object.fromEntries(['build', 'review'].map((node) => [`opencode-${node}`, { harnesses: ['opencode'], model: [harness.requestModel] }]));
    (nodes.build as Record<string, unknown>).session = 'resume:opencode-build';
    (nodes.review as Record<string, unknown>).session = 'fresh:opencode-review';
  }
  return JSON.stringify(definition);
}

for (const harness of harnesses) {
  test.describe(harness.name, () => {
    test.use({ isolation: 'fresh-tenant', ...(harness.name === 'opencode' ? { runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] } } : {}) });

    test('test_acceptance_loop_one_chunk_ingest_to_landed', async ({ api, forge, issue, repository, runnerHosts }) => {
      test.slow();
      const repo = await repository();
      const runnerHost = await runnerHosts.create({ runnerRepositories: [repo] });
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph(runnerHost, harness, repo.name) } })).status).toBe(201);
      const filed = await issue('land a change', { repository: repo });
      const chunkId = filed.chunk.chunk_id;
      expect((await api.get(`/api/chunks/${chunkId}`)).body.status).toBe('not_ready');
      expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
      expect((await api.get(`/api/chunks/${chunkId}`)).body.status).toBe('ready');
      expect(await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body.status, (status) => status === 'done')).toBe('done');
      const pulls = (await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`)).body as { merged: boolean; body: string }[];
      const merged = pulls.find((pull) => pull.merged);
      expect(merged).toBeDefined();
      expect(merged?.body).toContain(`Refs ${repo.owner}/${repo.name}#${filed.issue.number}`);
      expect(merged?.body).toContain(`Chunk: ${chunkId}`);
      expect(await runnerHost.originTree(repo.name, 'main')).toContain('LANDED.md');
      expect(await runnerHost.originMessage(repo.name, 'main')).toContain(`Refs ${repo.owner}/${repo.name}#${filed.issue.number}`);
    });
  });
}

test.describe('work item pass-through', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('test_build_worker_reads_work_item_through_the_passthrough', async ({ api, forge, repository, runnerHosts }) => {
    test.slow();
    const body = 'PASSTHROUGH-BODY: the widget flake reproduces under load';
    const comment = 'PASSTHROUGH-COMMENT: attached a failing repro in the linked gist';
    const repo = await repository();
    const runnerHost = await runnerHosts.create({ runnerRepositories: [repo] });
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(runnerHost, harnesses[0]!, repo.name, { passthrough: true }) } })).status).toBe(201);
    const filed = await forge.createIssue(`${repo.owner}/${repo.name}`, { title: 'pass-through', body });
    expect((await forge.call('POST', `/repos/${repo.owner}/${repo.name}/issues/${filed.number}/comments`, { body: comment })).status).toBe(201);
    const ingested = await api.post('/api/chunks', { data: { tokens: [`${repo.tokenPrefix}:${filed.number}`] } });
    const chunkId = ingested.body.chunk_id as string;
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    const item = (await api.get(`/api/chunks/${chunkId}/work-items`)).body.items as { body: string; comments: string[] }[];
    expect(item[0]).toMatchObject({ body, comments: [comment] });
    expect(await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunkId}`)).body.status, (status) => status === 'done')).toBe('done');
    const landed = await runnerHost.originFile(repo.name, 'main', 'LANDED.md');
    expect(landed).toContain(body);
    expect(landed).toContain(comment);
  });
});

for (const setting of [{ name: 'short', wait: 1 }, { name: 'whole-chunk', wait: 90 }]) {
  collectorTest.describe(setting.name, () => {
    collectorTest.describe.configure({ timeout: 180000 });
    collectorTest.use({ isolation: 'fresh-hub', collectorWait: setting.wait });
    collectorTest('test_a_tail_sampling_decision_wait_splits_or_keeps_the_chunk_trace', async ({ api, issue, repository, runnerHosts, workerCheckpoint, collector }) => {
      const repo = await repository();
      const host = await runnerHosts.create({ runnerRepositories: [repo] });
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph(host, harnesses[0]!, repo.name, { probed: true, checkpoint: workerCheckpoint.snippet('before-commit') }) } })).status).toBe(201);
      const { chunk } = await issue('whole chunk tail sampling', { repository: repo });
      await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
      let arrived = false;
      void workerCheckpoint.reached('before-commit').then(() => { arrived = true; });
      await stepUntil(host, async () => arrived, Boolean);
      await expect.poll(() => collector.received.spans().find((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id && span.scope.name === 'blizzard.cli')?.traceId, { timeout: 30000 }).toBeTruthy();
      const traceId = collector.received.spans().find((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id && span.scope.name === 'blizzard.cli')!.traceId;
      if (setting.name === 'short') await expect.poll(() => collector.decision(traceId), { timeout: 30000 }).toBe('dropped');
      workerCheckpoint.release('before-commit');
      await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');
      await expect.poll(async () => {
        await host.step();
        const spans = collector.received.spans().filter((span) => span.traceId === traceId);
        return [spans.filter(isFleet).filter((span) => roleOf(span) === 'step').length, spans.filter(isFleet).filter((span) => roleOf(span) === 'runner/invocation').length];
      }, { timeout: 30000 }).toEqual([3, 4]);
      const input = collector.received.spans().filter((span) => span.traceId === traceId);
      await expect.poll(() => {
        try { assertPlatformNesting(input.filter(isFleet), collector.received.spans().filter((span) => span.traceId === traceId && !isFleet(span))); return true; }
        catch { return false; }
      }, { timeout: 30000 }).toBe(true);
      if (setting.name === 'short') {
        const kept = collector.forwarded.spans().filter((span) => span.traceId === traceId);
        expect(kept.filter(isFleet).filter((span) => roleOf(span) === 'step')).toEqual([]);
        expect(kept.filter((span) => span.scope.name === 'blizzard.cli')).toEqual([]);
      } else {
        await expect.poll(() => collector.forwarded.spans().filter((span) => span.traceId === traceId && isFleet(span) && roleOf(span) === 'step').length, { timeout: 120000 }).toBe(3);
        await expect.poll(() => {
          const kept = collector.forwarded.spans().filter((span) => span.traceId === traceId);
          try { assertPlatformNesting(kept.filter(isFleet), kept.filter((span) => !isFleet(span))); return true; }
          catch { return false; }
        }, { timeout: 30000 }).toBe(true);
      }
    });
  });
}

traced('test_acceptance_loop_one_chunk_ingest_to_landed', async ({ api, issue, repository, runnerHosts, traceSink }) => {
  test.setTimeout(120000);
  const harness = harnesses[0]!;
  const repo = await repository();
  const runnerHost = await runnerHosts.create({ runnerRepositories: [repo] });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(runnerHost, harness, repo.name, { probed: true }) } })).status).toBe(201);
  const { chunk } = await issue('trace acceptance', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');
  await expect.poll(() => {
    const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id);
    return [spans.filter(isFleet).filter((span) => roleOf(span) === 'step').length, spans.filter(isFleet).filter((span) => roleOf(span) === 'runner/invocation').length, spans.some((span) => span.scope.name === 'blizzard.cli')];
  }, { timeout: 30000 }).toEqual([3, 4, true]);
  const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id);
  const fleet = spans.filter(isFleet);
  const traces = tracesOf(fleet);
  assertConforms(fleet);
  assertIdentity(traces);
  assertSkeleton(traces, [
    { name: 'step build', outcome: 'transitioned', toNode: 'review', children: ['queue wait', 'claim'] },
    { name: 'step review', outcome: 'transitioned', toNode: 'deliver', link: 'next' },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'done', children: ['hub exec'], link: 'next' },
  ] satisfies StepExpect[]);
  const roots = assertChunkTrace(fleet, chunk.chunk_id, { outcome: 'done', steps: 3 });
  expect(roots.work.attributes['blizzard.chunk.bounces']).toBe(0);
  const runners = assertRunnerNesting(fleet, 2);
  assertInvocations(runners, { build: { harnessId: harness.id, responseModel: harness.responseModel }, review: { harnessId: harness.id, responseModel: harness.responseModel } });
  let platformError: unknown;
  await expect.poll(() => {
    const exported = traceSink.spans().filter((span) => span.traceId === roots.work.traceId);
    try { assertPlatformNesting(exported.filter(isFleet), exported.filter((span) => !isFleet(span))); return true; }
    catch (error) { platformError = error; return false; }
  }, { timeout: 30000 }).toBe(true).catch(() => { throw platformError; });
  const complete = traceSink.spans().filter((span) => span.traceId === roots.work.traceId);
  assertPlatformNesting(complete.filter(isFleet), complete.filter((span) => !isFleet(span)));
  const tokens = (await runnerHost.mockSessions()).flatMap((session) => {
    const credential = session.spawn_credential as { lease_token?: string } | undefined;
    return credential?.lease_token ? [credential.lease_token] : [];
  });
  assertNoLeaks(traceSink.spans(), { 'lease token': tokens, 'runner bearer': [runnerHost.hubTokens().runners['runner-local']!], 'artifact body': [artifactBody] });
});
