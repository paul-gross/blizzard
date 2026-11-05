import { expect, test as base } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned.

// `GET /api/routines/{routine_id}/baselines` over a real hub: one entry per scope however many sweeps there are, and
// the 404 on an unknown routine. What lands against a baseline needs a real commit to land, so
// `tests/service/fleet/routine-baselines.spec.ts` owns it. FLEET_VIEW enforcement on the route is the permission
// matrix's own concern.

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
    /** One routine run's whole delivery: mint the run, claim it as the mock runner and walk it to the graph's `deliver` node, whose `garden-deliver` action records the delta; resolves to the choice the action answered. */
    deliver: (ops: unknown[]) => Promise<string | undefined>;
  };
}>({
  garden: async ({ api, forge, repository, mockRunner }, use) => {
    const repo = await repository();
    const commit = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/commits/main`);
    const head: string = commit.body.sha;

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

      const delta = JSON.stringify({ scope: SCOPE, revisions: { [repo.name]: head }, measurement: 'service sweep', findings: ops });
      const surveyed = await runner.drive('complete', {
        chunk_id: chunkId,
        choice: 'found',
        artifacts: [
          { name: 'survey', kind: 'asset', content: '{}', attached: true },
          { name: 'delta', kind: 'asset', content: delta, attached: true },
        ],
      });
      expect(surveyed.body.response?.outcome).toBe('next');
      const reconciled = await runner.drive('complete', {
        chunk_id: chunkId,
        choice: 'nothing-to-propose',
        artifacts: [{ name: 'delta', kind: 'asset', content: delta, attached: true }],
      });
      expect(reconciled.body.response?.outcome).toBe('hub_node_taken');

      // The deliver node's pass ends the run at `done` on `recorded`, or routes it back to a runner node.
      await expect
        .poll(async () => (await api.get(`/api/chunks/${chunkId}`)).body.current_node_name, { timeout: 60_000 })
        .not.toBe('deliver');
      const detail = await api.get(`/api/chunks/${chunkId}`);
      const exit = (detail.body.history as { from_node_name: string; choice_name: string | null }[])
        .filter((transition) => transition.from_node_name === 'deliver')
        .at(-1);
      return exit?.choice_name ?? undefined;
    };

    await use({ routineId, repoName: repo.name, head, deliver });
  },
});

test.describe('a delivered garden run', () => {
  // The runner and the repository the delivery needs are tenant-wide, so each test owns its tenant.
  test.use({ isolation: 'fresh-tenant' });

  test('test_baselines_after_a_second_sweep_still_serves_one_newest_entry', async ({ api, garden }) => {
    // One entry per scope, never one per sweep.
    for (const locus of ['src/app.py:0', 'src/app.py:second']) {
      expect(await garden.deliver([addOp(locus)])).toBe('recorded');
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
