import { expect, stepUntil, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_spike_chunk_terminates_with_only_asset_artifacts', async ({ repository, issue, api, forge, runnerHosts }) => {
  test.slow();
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const mainBefore = await host.originHead(repo.name, 'main');
  expect(mainBefore).toBeTruthy();
  const notes = 'SPIKE: investigated toy-api; the change is not warranted — no code needed.';
  const graph = {
    name: 'default-delivery', entry: 'spike', nodes: {
      spike: {
        executor: 'runner', prompt: 'pass\n', produces: ['spike-notes'],
        judgement: { prompt: `verdict('complete', ${JSON.stringify(notes)})\n`, choices: {
          complete: { description: 'The investigation is complete; findings recorded.', to: 'deliver' },
        } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' },
        conflict: { description: 'Conflict.', to: 'spike' },
        failure: { description: 'Failed.', to: 'spike' },
        'inherited-failure': { description: 'The base check stays red.', to: 'spike' },
      } } },
    },
  };
  expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph) } })).status).toBe(201);
  const { chunk } = await issue('spike toy-api', { repository: repo });
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body, (state) => state.status === 'done');
  expect(detail.status).toBe('done');
  const artifacts = detail.artifacts as { kind: string; name: string; content: string }[];
  expect(new Set(artifacts.map((artifact) => artifact.kind))).toEqual(new Set(['asset']));
  expect(artifacts.find((artifact) => artifact.name === 'spike-notes')?.content).toBe(notes);
  expect(artifacts.filter((artifact) => artifact.kind === 'git_commit')).toEqual([]);
  const pulls = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`);
  expect(pulls.status).toBe(200);
  expect(pulls.body).toEqual([]);
  expect(await host.originHead(repo.name, 'main')).toBe(mainBefore);
});
