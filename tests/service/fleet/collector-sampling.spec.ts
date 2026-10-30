import { collectorTest as test, expect, isFleet, roleOf, stepUntil } from '../../harness/fixtures';

for (const setting of [{ name: 'short', wait: 1 }, { name: 'whole', wait: 40 }]) {
  test.describe(setting.name, () => {
    test.describe.configure({ timeout: 120000 });
    test.use({ isolation: 'fresh-hub', collectorWait: setting.wait });
    test('tail sampling compares the collector input and output', async ({ api, issue, repository, runnerHost, collector, workerCheckpoint }) => {
      test.setTimeout(120000);
      const graph = JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
        build: { executor: 'runner', prompt: `import os, subprocess\nsubprocess.run([${JSON.stringify(runnerHost.cliBinary)}, 'runner', 'work-items', os.environ['BLIZZARD_CHUNK_ID']], check=True)\n` + workerCheckpoint.snippet('hold'), judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'done', to: 'done' } } }, retries: { max: 1, exhausted: 'escalate' } },
      } });
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).ok).toBe(true);
      const repo = await repository('toy-api');
      const { chunk } = await issue('collector sampling', { repository: repo });
      await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
      let arrived = false;
      void workerCheckpoint.reached('hold').then(() => { arrived = true; });
      await stepUntil(runnerHost, async () => arrived, Boolean);
      await expect.poll(() => collector.received.spans().some((s) => s.traceId && !isFleet(s)), { timeout: 30000 }).toBe(true);
      if (setting.name === 'short') await expect.poll(() => Date.now(), { timeout: 5000 }).toBeGreaterThan(Date.now() + 2000);
      workerCheckpoint.release('hold');
      await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (status) => status === 'done');
      await expect.poll(() => collector.received.spans().filter(isFleet).filter((s) => s.attributes['blizzard.chunk.id'] === chunk.chunk_id).some((s) => roleOf(s) === 'step'), { timeout: 30000 }).toBe(true);
      const root = collector.received.spans().filter(isFleet).find((s) => s.attributes['blizzard.chunk.id'] === chunk.chunk_id && roleOf(s) === 'step')!;
      expect(collector.received.spans().some((s) => s.traceId === root.traceId && !isFleet(s))).toBe(true);
      await expect.poll(async () => {
        await runnerHost.step();
        return collector.received.spans().filter(isFleet).some((s) => s.traceId === root.traceId && roleOf(s) === 'runner/invocation');
      }, { timeout: 30000 }).toBe(true);
      if (setting.name === 'whole') {
        await expect.poll(() => collector.forwarded.spans().some((s) => s.spanId === root.spanId), { timeout: 60000 }).toBe(true);
        await expect.poll(() => collector.forwarded.spans().filter(isFleet).some((s) => s.traceId === root.traceId && roleOf(s) === 'runner/invocation'), { timeout: 60000 }).toBe(true);
      } else {
        await expect.poll(() => Date.now(), { timeout: 5000 }).toBeGreaterThan(Date.now() + 2000);
        expect(collector.forwarded.spans().some((s) => s.spanId === root.spanId)).toBe(false);
      }
    });
  });
}
