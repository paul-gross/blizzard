import { expect, test } from '../../harness/fixtures';

// Changing daemon configuration and sweeping its entire event history require an owned hub.
test.use({ isolation: 'fresh-hub' });

test('test_a_legacy_pending_close_intent_is_terminally_skipped_after_its_closer_is_disabled', async ({ api, issue, freshHub, forge, repository }) => {
  const repo = await repository();
  const filed = await issue('legacy close intent', { repository: repo });
  const id = filed.chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/complete`, { data: { by: 'operator' } })).status).toBe(202);
  await freshHub.stop();
  await freshHub.start({ close_forge_writes_enabled: false });
  await api.sweep('close_drain');
  await api.sweep('close_drain');
  const events = (await api.get('/api/events', { params: { chunk_id: id } })).body.events;
  expect(events.filter((event: { kind: string }) => event.kind === 'work-item-close-skipped')).toEqual([
    expect.objectContaining({ chunk_id: id, severity: 'warning', detail: expect.objectContaining({ outcome: 'skipped', reason: 'no closer bound for source', ref: String(filed.issue.number) }) }),
  ]);
  await freshHub.stop();
  await freshHub.start({ close_forge_writes_enabled: true });
  await api.sweep('close_drain');
  expect((await forge.call('GET', `/repos/${repo.owner}/${repo.name}/issues/${filed.issue.number}`)).body.state).toBe('open');
  expect((await api.get('/api/admin/invariants')).body.violations).toEqual([]);
});

test('test_completion_without_a_closer_skips_once_and_a_bound_closer_still_closes', async ({ api, issue, freshHub, forge, cli, repository }) => {
  const repo = await repository();
  const skipped = await issue('skip at completion', { repository: repo });
  await freshHub.stop();
  await freshHub.start({ close_forge_writes_enabled: false });
  const id = skipped.chunk.chunk_id;
  expect((await cli(['hub', 'chunk', 'done', id])).code).toBe(0);
  expect((await api.get(`/api/chunks/${id}`)).body.status).toBe('done');
  expect((await api.post(`/api/chunks/${id}/complete`, { data: { by: 'replay' } })).status).toBe(202);
  await api.sweep('close_drain');
  const events = (await api.get('/api/events', { params: { chunk_id: id } })).body.events;
  expect(events.filter((event: { kind: string }) => event.kind === 'work-item-close-skipped')).toHaveLength(1);
  await freshHub.stop();
  await freshHub.start({ close_forge_writes_enabled: true });
  const bound = await issue('bound closer', { repository: repo });
  expect((await api.post(`/api/chunks/${bound.chunk.chunk_id}/complete`, { data: { by: 'operator' } })).status).toBe(202);
  await api.sweep('close_drain');
  expect((await forge.call('GET', `/repos/${repo.owner}/${repo.name}/issues/${skipped.issue.number}`)).body.state).toBe('open');
  expect((await forge.call('GET', `/repos/${repo.owner}/${repo.name}/issues/${bound.issue.number}`)).body.state).toBe('closed');
  expect((await api.get('/api/admin/invariants')).body.violations).toEqual([]);
});
