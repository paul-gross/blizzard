import { expect, test as base } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.

// `GET /api/routines/{routine_id}/baselines` over a real hub: the shape a delivered garden run's baseline serves, one
// entry per scope however many sweeps there are, and the 404 on an unknown routine. FLEET_VIEW enforcement on the
// route is the permission matrix's own concern.

const SCOPE = 'garden-svc';

function addOp(locus: string) {
  return { op: 'add', class: 'stale-docstring', locus, summary: `weed at ${locus}` };
}

const test = base.extend<{
  /** A garden routine on the tenant's packaged `garden-routine` graph, a repository for its deltas to name, and a runner to claim its runs. */
  garden: {
    routineId: string;
    repoName: string;
    head: string;
    /** One routine run's whole delivery: mint the run, claim it as the mock runner, submit its artifacts, then post the hub's garden-delivery route. */
    deliver: (ops: unknown[]) => Promise<{ status: number; body: { outcome: string } }>;
    /** Posts a `merged/<repo>` hub-marker on a throwaway chunk: the one production choke point that records a repo landing, which the garden graph's own deliver node never does. */
    land: (commit: string) => Promise<void>;
  };
}>({
  garden: async ({ api, forge, repository, mockRunner }, use) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    const head: string = commit.body.sha;

    // Tenant creation mints the packaged graphs, so the test finds the garden graph rather than minting one.
    const listed = await api.get('/api/graphs');
    const summary = listed.body.find((graph: { name: string; effective: boolean }) => graph.name === 'garden-routine' && graph.effective);
    expect(summary).toBeDefined();
    const graph = await api.get(`/api/graphs/${summary.graph_id}`);
    const deliverNode = graph.body.nodes.find((node: { name: string }) => node.name === 'deliver');
    expect(deliverNode).toBeDefined();

    const created = await api.post('/api/routines', {
      data: { name: 'garden-service', graph_name: 'garden-routine', default_scope_slug: SCOPE, default_model: [], default_effort: null },
    });
    expect(created.status).toBe(201);
    const routineId: string = created.body.routine_id;
    const runner = await mockRunner('garden');
    expect((await runner.drive('register')).body.status).toBe(201);

    const deliver = async (ops: unknown[]) => {
      const run = await api.post(`/api/routines/${routineId}/run`, { data: { scope_slug: SCOPE, mode: 'full', note: 'sweep' } });
      expect(run.status).toBe(201);
      const chunkId: string = run.body.chunk_id;
      expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
      expect((await runner.drive('claim', { chunk_id: chunkId })).body.claimed).toBe(true);

      const delta = { scope: SCOPE, revisions: { [repo.name]: head }, measurement: 'service sweep', findings: ops };
      const completed = await runner.drive('complete', {
        chunk_id: chunkId,
        choice: 'found',
        artifacts: [
          { name: 'survey', kind: 'asset', content: '{}', attached: true },
          { name: 'delta', kind: 'asset', content: JSON.stringify(delta), attached: true },
        ],
      });
      expect(completed.body.response?.outcome).toBe('next');

      const detail = await api.get(`/api/chunks/${chunkId}`);
      return api.post(`/api/chunks/${chunkId}/garden-delivery`, {
        params: { node_id: deliverNode.node_id, epoch: detail.body.latest_epoch },
        data: { delta: ['delta'], proposals: [] },
      });
    };

    const land = async (landed: string) => {
      const minted = await api.post(`/api/routines/${routineId}/run`, { data: { scope_slug: SCOPE, mode: 'full', note: 'land' } });
      expect(minted.status).toBe(201);
      const chunkId: string = minted.body.chunk_id;
      const detail = await api.get(`/api/chunks/${chunkId}`);
      const chunkGraph = await api.get(`/api/graphs/${detail.body.graph_id}`);
      const marked = await api.post(`/api/chunks/${chunkId}/hub-markers`, {
        params: { node_id: chunkGraph.body.entry_node_id, epoch: 1 },
        data: { name: `merged/${repo.name}`, content: landed },
      });
      expect(marked.status).toBe(200);
      expect(marked.body.recorded).toBe(true);
    };

    await use({ routineId, repoName: repo.name, head, deliver, land });
  },
});

test.describe('a delivered garden run', () => {
  // The runner and the repository the delivery needs are tenant-wide, so each test owns its tenant.
  test.use({ isolation: 'fresh-tenant' });

  test('test_baselines_serves_the_swept_scopes_recorded_revision', async ({ api, garden }) => {
    const recorded = await garden.deliver([addOp('src/app.py:0')]);
    expect(recorded.status).toBe(200);
    expect(recorded.body.outcome).toBe('recorded');
    await garden.land('deadbeef');

    const response = await api.get(`/api/routines/${garden.routineId}/baselines`);

    expect(response.status).toBe(200);
    expect(response.body).toHaveLength(1);
    const [baseline] = response.body;
    expect(baseline.scope_slug).toBe(SCOPE);
    expect(baseline.finding_set_id).toMatch(/^fins_/);
    expect(baseline.recorded_at).toBeTruthy();
    expect(baseline.repos).toEqual([{ repo: garden.repoName, revision: garden.head, landed_since: 1 }]);
  });

  test('test_baselines_after_a_second_sweep_still_serves_one_newest_entry', async ({ api, garden }) => {
    // One entry per scope, never one per sweep.
    for (const locus of ['src/app.py:0', 'src/app.py:second']) {
      const recorded = await garden.deliver([addOp(locus)]);
      expect(recorded.status).toBe(200);
      expect(recorded.body.outcome).toBe('recorded');
    }

    const baselines = await api.get(`/api/routines/${garden.routineId}/baselines`);

    expect(baselines.body).toHaveLength(1);
    expect(baselines.body[0].scope_slug).toBe(SCOPE);
  });
});

base.describe('an unknown routine', () => {
  // Nothing is created, and the id read is one no test holds.
  base.use({ isolation: 'common' });

  base('test_baselines_unknown_routine_is_404', async ({ api }) => {
    const response = await api.get('/api/routines/rtn_ghost/baselines');

    expect(response.status).toBe(404);
  });
});
