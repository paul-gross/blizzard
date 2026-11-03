import fs from 'node:fs';
import { expect, test } from '../../harness/fixtures';

// The event feed is tenant-wide, and config apply names fixed records.
test.use({ isolation: 'fresh-tenant' });

test('test_the_old_fields_resolve_to_forges_with_a_warning_at_every_door', async ({ api, cli }, testInfo) => {
  expect((await api.post('/api/secrets', { data: { name: 'fixture-forge-token', value: 'fixture-token' } })).status).toBe(201);
  const ghe = 'https://ghe.acme.internal/api/v3';
  const source = { slug: 'enterprise-issues', provider: 'github', locator: 'acme/issues', api_base: ghe,
    web_base: 'https://ghe.acme.internal', secret: 'fixture-forge-token' };
  const document = { version: 1, work_sources: [source], repositories: [{ slug: 'enterprise-widget', forge_api_url: ghe,
    owner: 'acme', repo: 'widget', base_branch: 'main', secret_name: 'fixture-forge-token' }] };
  const write = (name: string, value: object) => {
    const path = testInfo.outputPath(name);
    fs.writeFileSync(path, JSON.stringify(value)); // JSON is valid YAML.
    return path;
  };
  const oldEvents = async () => {
    const response = await api.get('/api/events?severity=warning');
    expect(response.status).toBe(200);
    return response.body.events.filter((event: { kind: string }) => event.kind === 'config-old-field-used').reverse();
  };
  const seeded = (await oldEvents()).length;
  const applied = await cli(['hub', 'config', 'apply', write('before.yaml', document)]);
  expect(applied.code, applied.stderr).toBe(0);
  expect(applied.stderr.split('\n').filter((line) => line.startsWith('warning: '))).toHaveLength(3);
  const events = (await oldEvents()).slice(seeded).map((event: { detail: { field: string; forge_slug: string } }) => event.detail);
  expect(events.map((event: { field: string }) => event.field)).toEqual(['api_base', 'web_base', 'forge_api_url']);
  expect(new Set(events.map((event: { forge_slug: string }) => event.forge_slug))).toEqual(new Set(['ghe-acme-internal']));
  const readSource = await api.get('/api/work-sources/enterprise-issues');
  const readRepo = await api.get('/api/repositories/enterprise-widget');
  expect(readSource.status).toBe(200);
  expect(readRepo.status).toBe(200);
  expect(readSource.body.forge_id).toBe(readRepo.body.forge_id);
  expect([readSource.body.forge_slug, readSource.body.api_base, readSource.body.web_base]).toEqual([
    'ghe-acme-internal', ghe, 'https://ghe.acme.internal',
  ]);

  const { api_base: _api, web_base: _web, ...withoutBases } = source;
  const kept = await cli(['hub', 'config', 'apply', write('silent.yaml', {
    version: 1, work_sources: [{ ...withoutBases, annotate: true }],
  })]);
  expect(kept.code, kept.stderr).toBe(0);
  expect(kept.stderr).not.toContain('warning: ');
  expect((await api.get('/api/work-sources/enterprise-issues')).body.forge_slug).toBe('ghe-acme-internal');

  const cleared = await cli(['hub', 'source', 'edit', 'enterprise-issues', '--clear', 'api-base']);
  expect(cleared.code, cleared.stderr).toBe(0);
  expect(cleared.stderr.split('\n').some((line) => line.includes('api_base') && line.includes('github'))).toBe(true);
  const moved = await api.get('/api/work-sources/enterprise-issues');
  expect(moved.status).toBe(200);
  expect([moved.body.forge_slug, moved.body.api_base]).toEqual(['github', 'https://api.github.com']);
});
