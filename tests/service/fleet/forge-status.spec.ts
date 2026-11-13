import { expect, stepChunkUntil, stepUntilHeld, test } from '../../harness/fixtures';

// The forge-status projection, end to end: a chunk's status shows on its forge issue as a label and clears when the chunk
// leaves the live set, a label deleted by hand comes back, and a forge outage degrades to a recorded skip. Every pass of
// the hub's `forge_status` sweep is stepped; the waits whose subject is the full-pass floor advance the stack's clock
// past it.

/**
 * The hub reads the forge afresh once the full-pass floor has gone by with nothing changed on the hub side:
 * `FULL_PASS_FLOOR_INTERVALS` (5) × `DEFAULT_ANNOTATION_INTERVAL_SECONDS` (120).
 */
const FLOOR_SECONDS = 600;

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('test_forge_status_projection_e2e', async ({ api, clock, forge, issue, repository, runnerHost, workerCheckpoint }) => {
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
    await stepUntilHeld(runnerHost, a.chunkId, workerCheckpoint, 'hold');
    expect(await swept(a.number)).toEqual(['blizzard:in-progress']);
    workerCheckpoint.release('hold');
    await stepChunkUntil(runnerHost, api, a.chunkId, (status) => status === 'done');
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
    await clock.advance(FLOOR_SECONDS);
    expect(await swept(c.number), 'the full pass did not re-assert the hand-deleted label').toEqual(['blizzard:ingested']);

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
      await clock.advance(FLOOR_SECONDS);
      await sweep();
      const feed = (await api.get('/api/events')).body as { events: { kind: string; detail?: { work_source_id?: string } }[] };
      const skips = feed.events.filter((event) => event.kind === 'forge-status-source-skipped' && event.detail?.work_source_id === sourceId);
      expect(skips, 'the sweep should have recorded a skip naming the source while the forge was unreachable').toHaveLength(1);
      // The hub itself is unaffected: chunk reads are never blocked on the forge being reachable.
      expect((await api.get(`/api/chunks/${d.chunkId}`)).status).toBe(200);
    } finally {
      expect((await forge.call('DELETE', '/_levers/unreachable')).status).toBe(200);
    }
    await clock.advance(FLOOR_SECONDS);
    expect(await swept(d.number), 'the projection did not re-converge once the forge returned').toEqual(['blizzard:ingested']);
  });

  test('test_forge_status_records_a_refused_credential_naming_the_work_source', async ({ api, issue, repository, forge, hubLog }) => {
    const repo = await repository('refused-api');
    const optedIn = await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { annotate: true } });
    expect(optedIn.ok, JSON.stringify(optedIn.body)).toBe(true);
    const source = await api.get(`/api/work-sources/${repo.tokenPrefix}`);
    const sourceId = (source.body as { work_source_id: string }).work_source_id;
    expect(sourceId).toBeTruthy();
    const { chunk } = await issue('refuse my sweep', { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);

    // Armed hub-wide, so the work-item read's GraphQL call, which names no repository in its path, is refused too.
    expect((await forge.call('POST', '/_levers/token_rejected', {})).status).toBe(200);
    try {
      await api.sweep('forge_status', { allTenants: true });
      type Refused = { kind: string; detail?: { work_source_id?: string; forge_id?: string; refusal?: string; diagnostic?: { status?: number } } };
      const feed = (await api.get('/api/events?limit=200')).body as { events: Refused[] };
      const refused = feed.events.filter((event) => event.kind === 'forge-auth-refused' && event.detail?.work_source_id === sourceId);
      expect(refused, 'the sweep should have recorded one refusal naming the work source').toHaveLength(1);
      expect(refused[0]!.detail?.refusal).toBe('credential-invalid');
      expect(refused[0]!.detail?.forge_id).toBeTruthy();
      expect(refused[0]!.detail?.diagnostic?.status).toBe(401);

      const items = (await api.get(`/api/chunks/${chunk.chunk_id}/work-items`)).body as { items: { error: string | null }[] };
      const error = items.items[0]?.error ?? '';
      expect(error).toContain('the token is wrong, expired, or revoked');
      expect(error).toContain('Replace the token, or check the App\'s installation.');

      expect(JSON.stringify(feed)).not.toContain('fixture-token');
      expect(JSON.stringify(items)).not.toContain('fixture-token');
      expect(hubLog.lines().filter((line) => line.raw.includes('fixture-token'))).toEqual([]);
    } finally {
      expect((await forge.call('DELETE', '/_levers/token_rejected')).status).toBe(200);
    }
  });
});
