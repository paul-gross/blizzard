import { parse } from 'smol-toml';
import { expect, test } from '../../harness/fixtures';

test.use({ isolation: 'fresh-tenant', runnerStarted: false });

test('test_a_freshly_initialized_runner_is_offered_a_default_chunk_on_first_registration', async ({ runnerHost, api, issue, bearer, project }) => {
  const projects = (await api.get('/api/projects')).body.projects as { slug: string; project_id: string }[];
  const defaultProject = projects.find((p) => p.slug === 'default')!;
  expect(defaultProject.project_id).toBe(project.id);
  const { chunk } = await issue('first contact');
  const runner = runnerHost.runners['runner-local']!;
  const scaffold = parse(runnerHost.initHostFile);
  const configured = scaffold.runner as { projects: { project: string }[] }[];
  expect(configured[0]!.projects.map((p) => p.project)).toEqual(['default']);
  const overlaid = runnerHost.hostFile();
  const added = await api.get(`/api/runners/${runner.id}`);
  expect(added.status).toBe(200);
  expect(added.body.registered_at).toBeNull();
  await runnerHost.restart();
  expect((await runnerHost.step()).outcome).toBe('completed');
  expect(runnerHost.hostFile()).toBe(overlaid);
  const registered = (await api.get(`/api/runners/${runner.id}`)).body;
  expect(registered.registered_at).not.toBeNull();
  expect((registered.projects as { entry: string; project_id: string }[]).map((p) => [p.entry, p.project_id])).toEqual([['default', defaultProject.project_id]]);
  const status = await runner.status();
  expect(status.status).toBe(200);
  expect(status.body.served_projects?.map((p) => [p.entry, p.project_id])).toEqual([['default', defaultProject.project_id]]);
  expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
  const token = runnerHost.hubTokens().runners['runner-local']!;
  const peek = await (await bearer(token)).post('/api/fleet/queue/peek', { data: { capabilities: [{ harness_id: 'claude', default: true }] } });
  expect(peek.status).toBe(200);
  expect((peek.body.entries as { chunk_id: string; project_id: string; project_slug: string }[]).map((e) => [e.chunk_id, e.project_id, e.project_slug])).toEqual([[chunk.chunk_id, defaultProject.project_id, 'default']]);
});
