import { type ChunkWait, assertInvocations, assertRunnerNesting, assertSkeleton, controlledHubWait, expect, isFleet, roleOf, stepChunkVia, type StepExpect, test, traced, tracesOf } from '../../harness/fixtures';

const harnesses = [
  { name: 'claude-code', id: 'claude_code', model: 'claude-opus-4-8', sessionFlag: '--resume' },
  { name: 'opencode', id: 'opencode', model: 'mock-provider/opencode-e2e', sessionFlag: '--session' },
];

function graph(harness: typeof harnesses[number]): string {
  const nodes: Record<string, unknown> = {
    build: { executor: 'runner', prompt: 'pass\n', judgement: { prompt: 'pass\n', choices: { pass: { description: 'Committed and green.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  };
  const definition: Record<string, unknown> = { name: 'default-delivery', entry: 'build', nodes };
  if (harness.name === 'opencode') {
    definition.sessions = { 'opencode-build': { harnesses: ['opencode'], model: [harness.model] } };
    (nodes.build as Record<string, unknown>).session = 'resume:opencode-build';
  }
  return JSON.stringify(definition);
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'issue' | 'repository' | 'runnerHosts'>;
async function escalated({ api, issue, repository, runnerHosts }: World, harness: typeof harnesses[number], wait?: ChunkWait) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! }, ...(harness.name === 'opencode' ? { runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }] } } : {}) });
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(harness) } })).status).toBe(201);
  const { chunk } = await issue('escalation', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  const status = await stepChunkVia(host, api, chunk.chunk_id, (value) => ['needs_human', 'done', 'stopped'].includes(value), wait);
  const detail = (await api.get(`/api/chunks/${chunk.chunk_id}`)).body;
  return { host, chunk, status, detail };
}

for (const harness of harnesses) {
  test.describe(harness.name, () => {
    test.use({ isolation: 'fresh-tenant' });
    test('test_retries_exhausted_escalates_and_takeover_resumes_session', async ({ api, issue, repository, runnerHosts }) => {
      test.setTimeout(120000);
      const state = await escalated({ api, issue, repository, runnerHosts }, harness);
      expect(state.status).toBe('needs_human');
      const attempts = (await state.host.runners['runner-local']!.leases()).body.items.filter((lease) => lease.chunk_id === state.chunk.chunk_id && lease.node_name === 'build');
      expect(attempts).toHaveLength(2);
      const escalation = state.detail.escalation as { takeover_command: string; wrapped_takeover_command: string };
      expect(escalation.takeover_command).toBeTruthy();
      expect(escalation.wrapped_takeover_command).toBe(`blizzard runner takeover ${state.chunk.chunk_id} --dir ${state.host.dir} --runner runner-local`);
      const sessionId = new RegExp(`${harness.sessionFlag} (\\S+)`).exec(escalation.takeover_command)?.[1];
      expect(sessionId).toBeTruthy();
      const before = (await state.host.mockSessions()).find((session) => session.session_id === sessionId);
      expect(before).toBeDefined();
      const marker = `human takeover ${state.chunk.chunk_id}`;
      const result = await state.host.shell(escalation.takeover_command, `# ${marker}\n`);
      expect(result.code, result.stderr).toBe(0);
      const after = (await state.host.mockSessions()).find((session) => session.session_id === sessionId);
      if (harness.name === 'opencode') expect(result.stdout).toContain(sessionId!);
      else {
        expect(after?.turns as number).toBe((before?.turns as number) + 1);
        expect((after?.resumes as string[]).some((resume) => resume.includes(marker))).toBe(true);
      }
    });
  });
}

test.describe('trace companion', () => {
  test.use({ clockMode: 'controlled' });
  traced('test_retries_exhausted_escalates_and_takeover_resumes_session', async ({ api, clock, flushTraces, issue, repository, runnerHosts, traceSink }) => {
    test.setTimeout(120000);
    // A controlled clock moves only when a turn moves it: the runner's trace cursor opens at its first pass's instant,
    // and a lease closing on that very instant can be passed over, so each read of the wait moves the clock on.
    const state = await escalated({ api, issue, repository, runnerHosts }, harnesses[0]!, controlledHubWait(api, clock));
    await flushTraces(state.host);
    const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
    expect([spans.filter((span) => roleOf(span) === 'step').length, spans.filter((span) => roleOf(span) === 'runner/invocation').length]).toEqual([2, 4]);
    assertSkeleton(tracesOf(spans), [
      { name: 'step build', outcome: 'superseded', children: ['queue wait', 'claim'] },
      { name: 'step build', outcome: 'escalated', status: 2, link: 'retry' },
    ] satisfies StepExpect[]);
    assertInvocations(assertRunnerNesting(spans, 2), { build: { harnessId: 'claude_code', responseModel: harnesses[0]!.model } });
  });
});
