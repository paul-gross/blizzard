import { assertIdentity, assertRunnerNesting, expect, isFleet, roleOf, stepUntil, test, traced, tracesOf } from '../fixtures';

const graph = JSON.stringify({
  name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: 'pass\n', judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'done', to: 'done' } } }, retries: { max: 1, exhausted: 'escalate' } },
  },
});

test.describe('shared hub', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('the shared hub does not use a test sink', async ({ testHub, otlpSink, api }) => {
    const sink = await otlpSink();
    expect(testHub.fresh).toBe(false);
    expect((await api.get('/api/traces/status')).body.state).toBe('disabled');
    expect(sink.spans()).toEqual([]);
  });
});

traced('a stepped runner completes a chunk', async ({ api, issue, repository, runnerHost, traceSink }) => {
  test.slow();
  const created = await api.post('/api/graphs', { data: { definition_yaml: graph } });
  if (!created.ok) throw new Error(`creating the graph answered ${created.status}`);
  const repo = await repository('toy-api');
  const { chunk } = await issue('trace companion', { repository: repo });
  await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');
  await expect.poll(() => {
    const spans = traceSink.spans().filter(isFleet).filter((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id);
    return [spans.some((span) => roleOf(span) === 'step'), spans.some((span) => roleOf(span) === 'runner/invocation')];
  }, { timeout: 30000 }).toEqual([true, true]);
  const spans = traceSink.spans().filter(isFleet).filter((span) => span.attributes['blizzard.chunk.id'] === chunk.chunk_id);
  assertIdentity(tracesOf(spans));
  assertRunnerNesting(spans, 1);
});
