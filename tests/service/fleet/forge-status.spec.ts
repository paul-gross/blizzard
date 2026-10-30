import { expect, test, stepUntil } from '../../harness/fixtures';

// The forge-status projection, end to end: a chunk's status shows on its forge issue as a label and clears when the chunk
// leaves the live set, a label deleted by hand comes back, and a forge outage degrades to a recorded skip. Every pass of
// the hub's `forge_status` sweep is stepped; the only clock waits are the two whose subject is the full-pass floor.

const INTERVAL_SECONDS = 1n;
/** The hub reads the forge afresh once this many intervals have gone by with nothing changed on the hub side. */
const FLOOR_SECONDS = 5 * Number(INTERVAL_SECONDS);
const FLOOR_DEADLINE_MS = (FLOOR_SECONDS + 25) * 1000;

test.describe('at fresh-hub', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: async ({ hubConfig }, use) => {
      await use({ ...hubConfig, annotation_interval_seconds: INTERVAL_SECONDS });
    },
  });

  test('test_forge_status_projection_e2e', async ({ api, forge, issue, repository, runnerHost, workerCheckpoint }) => {
    test.slow();
    const graph = JSON.stringify({
      name: 'default-delivery',
      entry: 'build',
      nodes: {
        build: {
          executor: 'runner',
          prompt: workerCheckpoint.snippet('hold'),
          judgement: { prompt: "verdict('pass', 'done')\n", choices: { pass: { description: 'done', to: 'done' } } },
          retries: { max: 1, exhausted: 'escalate' },
        },
      },
    });
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).ok).toBe(true);
    const repo = await repository('toy-api');
    const optedIn = await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { annotate: true } });
    expect(optedIn.ok, JSON.stringify(optedIn.body)).toBe(true);

    const sweep = (): Promise<unknown> => api.sweep('forge_status', { allTenants: true });
    const labels = async (number: number): Promise<string[]> => {
      const answer = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/issues/${number}`);
      expect(answer.status, JSON.stringify(answer.body)).toBe(200);
      return ((answer.body as { labels: { name: string }[] }).labels.map((label) => label.name)).sort();
    };
    // One stepped pass, then the labels it left.
    const swept = async (number: number): Promise<string[]> => {
      await sweep();
      return labels(number);
    };
    const filed = async (title: string) => {
      const { issue: filedIssue, chunk } = await issue(title, { repository: repo });
      expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
      return { chunkId: chunk.chunk_id, number: filedIssue.number };
    };

    // -- ingested -> in-progress while a worker holds the lease -> cleared on done --
    const a = await filed('sweep me end to end');
    expect(await swept(a.number)).toEqual(['blizzard:ingested']);
    let arrived = false;
    void workerCheckpoint.reached('hold').then(() => { arrived = true; });
    await stepUntil(runnerHost, async () => arrived, Boolean);
    expect(await swept(a.number)).toEqual(['blizzard:in-progress']);
    workerCheckpoint.release('hold');
    await stepUntil(runnerHost, async () => (await api.get(`/api/chunks/${a.chunkId}`)).body.status, (status) => status === 'done');
    expect(await swept(a.number)).toEqual([]);

    // -- a chunk stopped before a runner claims it clears its marker --
    const b = await filed('stop me before a runner ever claims me');
    expect(await swept(b.number)).toEqual(['blizzard:ingested']);
    expect((await api.post(`/api/chunks/${b.chunkId}/stop`, { data: { by: 'e2e-test' } })).status).toBe(202);
    expect(await swept(b.number)).toEqual([]);

    // -- a label deleted by hand: the hub's probe sees no hub-side change, so only the full-pass floor brings it back --
    const c = await filed('re-assert my hand-deleted label');
    expect(await swept(c.number)).toEqual(['blizzard:ingested']);
    const deleted = await forge.call('DELETE', `/repos/${repo.owner}/${repo.name}/issues/${c.number}/labels/blizzard:ingested`);
    expect(deleted.status, JSON.stringify(deleted.body)).toBe(200);
    expect(await labels(c.number)).toEqual([]);
    await expect.poll(() => swept(c.number), {
      message: 'the full pass did not re-assert the hand-deleted label',
      timeout: FLOOR_DEADLINE_MS,
    }).toEqual(['blizzard:ingested']);

    // -- a forge outage degrades to a recorded skip, and the projection re-converges --
    // The `unreachable` lever stands in for a process kill, which would also wipe the forge's state.
    const d = await filed('outlive a forge outage');
    expect(await swept(d.number)).toEqual(['blizzard:ingested']);
    const source = await api.get(`/api/work-sources/${repo.tokenPrefix}`);
    expect(source.status).toBe(200);
    const sourceId = (source.body as { work_source_id: string }).work_source_id;
    expect(sourceId).toBeTruthy();

    expect((await forge.call('POST', '/_levers/unreachable', {})).status).toBe(200);
    try {
      // Only a full pass reads the forge, so only one landing inside the outage records the skip.
      await expect.poll(async () => {
        await sweep();
        const feed = (await api.get('/api/events')).body as { events: { kind: string; detail?: { work_source_id?: string } }[] };
        return feed.events.some((event) => event.kind === 'forge-status-source-skipped' && event.detail?.work_source_id === sourceId);
      }, { message: 'the sweep should have recorded a skip naming the source while the forge was unreachable', timeout: FLOOR_DEADLINE_MS }).toBe(true);
      // The hub itself is unaffected: chunk reads are never blocked on the forge being reachable.
      expect((await api.get(`/api/chunks/${d.chunkId}`)).status).toBe(200);
    } finally {
      expect((await forge.call('DELETE', '/_levers/unreachable')).status).toBe(200);
    }
    await expect.poll(() => swept(d.number), {
      message: 'the projection did not re-converge once the forge returned',
      timeout: FLOOR_DEADLINE_MS,
    }).toEqual(['blizzard:ingested']);
  });
});
