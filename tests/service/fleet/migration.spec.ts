import { assertSkeleton, expect, isFleet, migratedChunk, roleOf, test, traced, tracesOf, type StepExpect } from '../../harness/fixtures';

test.describe('cross-graph migration', () => {
  test.use({ isolation: 'fresh-tenant' });
  test('test_cross_graph_migration_repins_requeues_and_lands_under_the_new_graph', async ({ api, forge, issue, repository, runnerHosts }) => {
    test.setTimeout(120000);
    const state = await migratedChunk({ api, issue, repository, runnerHosts });
    expect(state.status).toBe('done');
    const detail = (await api.get(`/api/chunks/${state.chunk.chunk_id}`)).body;
    expect(detail.graph_id).toBe(state.targetGraphId);
    expect(detail.migrations).toHaveLength(1);
    expect(detail.migrations[0]).toMatchObject({ from_node_name: 'build', to_graph_name: 'triage-delivery', landed_node_name: 'build' });
    const history = detail.history as { from_node_name: string; to_node_name?: string; to_node_id?: string; choice_name: string }[];
    expect(history.some((step) => step.from_node_name === 'build' && step.to_node_name === 'deliver' && step.choice_name === 'pass')).toBe(true);
    expect(history.some((step) => step.from_node_name === 'deliver' && step.to_node_id === 'done' && step.choice_name === 'landed')).toBe(true);
    const pulls = (await forge.call('GET', `/repos/${state.repo.owner}/${state.repo.name}/pulls?state=all`)).body as { merged: boolean }[];
    expect(pulls.some((pull) => pull.merged)).toBe(true);
    expect(await state.host.originTree(state.repo.name, 'main')).toContain('LANDED.md');
    expect(await state.host.originLog(state.repo.name, 'main', 'LANDED.md')).toHaveLength(1);
  });
});

traced('test_cross_graph_migration_repins_requeues_and_lands_under_the_new_graph', async ({ api, issue, repository, runnerHosts, traceSink }) => {
  test.setTimeout(120000);
  const state = await migratedChunk({ api, issue, repository, runnerHosts });
  await expect.poll(() => {
    const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
    return [spans.filter((span) => roleOf(span) === 'step').length, spans.filter((span) => roleOf(span) === 'runner/invocation').length];
  }, { timeout: 30000 }).toEqual([3, 4]);
  const spans = traceSink.spans().filter((span) => span.attributes['blizzard.chunk.id'] === state.chunk.chunk_id && isFleet(span));
  assertSkeleton(tracesOf(spans), [
    { name: 'step build', outcome: 'migrated', toNode: 'graph:triage-delivery', children: ['queue wait', 'claim'], attributes: { 'blizzard.graph.name': 'default-delivery' } },
    { name: 'step build', outcome: 'transitioned', toNode: 'deliver', children: ['queue wait', 'claim'], link: 'migration', attributes: { 'blizzard.graph.name': 'triage-delivery' } },
    { name: 'step deliver', outcome: 'transitioned', toNode: 'done', children: ['hub exec'], link: 'next' },
  ] satisfies StepExpect[]);
});
