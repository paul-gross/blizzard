import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-hub' });

test('test_a_hub_without_the_test_fences_refuses_the_test_actions', async ({ api }) => {
  const graph = JSON.stringify({ name: 'default-delivery', entry: 'pick', nodes: {
    pick: { executor: 'hub', action: 'test.answer', with: { choice: 'onward' }, judgement: { choices: { onward: { description: 'Onward.', to: 'walk' } } } },
    walk: { executor: 'hub', action: 'test.sequence', with: { choices: ['pending', 'done'] }, judgement: { choices: { done: { description: 'Done.', to: 'done' } } } },
  } });
  const refused = await api.post('/api/graphs', { data: { definition_yaml: graph } });
  expect(refused.status).toBe(422);
  for (const action of ['test.answer', 'test.sequence']) expect(JSON.stringify(refused.body)).toContain(`unknown action \`${action}\``);
});
