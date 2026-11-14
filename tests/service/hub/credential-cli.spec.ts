import { expect, test } from '../../harness/fixtures';

// The real `blizzard` binary drives a credential through every verb against a running hub, and a repository created
// with only the old `--secret` still lands, warning on stderr. Each test writes the tenant's configuration, so each owns
// its tenant.
test.use({ isolation: 'fresh-tenant' });

const SECRET = 'credential-cli-token';

test('test_each_credential_verb_round_trips_a_credential_and_the_change_log_lists_it', async ({ api, cli, forge }) => {
  expect((await api.post('/api/secrets', { data: { name: SECRET, value: 'fixture-token' } })).status).toBe(201);
  expect((await cli(['hub', 'forge', 'create', 'cred-forge', '--kind', 'github', '--api-base', forge.url])).code).toBe(0);

  const created = await cli([
    'hub', 'credential', 'create', 'gh', '--forge', 'cred-forge', '--binding', 'github.token', '--secret', SECRET,
  ]);
  expect(created.code, created.stderr).toBe(0);
  expect(created.stdout).toContain('credential gh created at revision 1');

  const listed = await cli(['hub', 'credential', 'list', '--json']);
  expect(listed.code, listed.stderr).toBe(0);
  expect(JSON.parse(listed.stdout).map((row: { slug: string }) => row.slug)).toEqual(['gh']);

  const shown = await cli(['hub', 'credential', 'get', 'gh', '--json']);
  expect(shown.code, shown.stderr).toBe(0);
  expect(JSON.parse(shown.stdout)).toEqual((await api.get('/api/credentials/gh')).body);

  const edited = await cli(['hub', 'credential', 'edit', 'gh', '--name', 'GitHub', '--variant', 'classic', '--if-match', '1']);
  expect(edited.code, edited.stderr).toBe(0);
  expect(edited.stdout).toContain('revision 2');

  const retired = await cli(['hub', 'credential', 'retire', 'gh']);
  expect(retired.code, retired.stderr).toBe(0);
  expect(retired.stdout).toContain('retired');
  const enabled = await cli(['hub', 'credential', 'enable', 'gh']);
  expect(enabled.code, enabled.stderr).toBe(0);
  expect(enabled.stdout).toContain('enabled');

  const changes = await cli(['hub', 'config', 'changes', '--kind', 'credential', '--all', '--json']);
  expect(changes.code, changes.stderr).toBe(0);
  expect(JSON.parse(changes.stdout).changes.map((change: { op: string }) => change.op)).toEqual([
    'enable', 'retire', 'edit', 'create',
  ]);
});

test('test_a_repo_created_with_only_the_old_secret_warns_on_stderr_and_exits_0', async ({ api, cli, forge }) => {
  expect((await api.post('/api/secrets', { data: { name: SECRET, value: 'fixture-token' } })).status).toBe(201);
  expect((await cli(['hub', 'forge', 'create', 'cred-forge', '--kind', 'github', '--api-base', forge.url])).code).toBe(0);

  const created = await cli([
    'hub', 'repo', 'create', 'old-field', '--forge', 'cred-forge', '--owner', 'blizzard', '--repo', 'old-field',
    '--base-branch', 'main', '--secret', SECRET,
  ]);

  expect(created.code, created.stderr).toBe(0);
  expect(created.stderr).toContain('warning: ');
  expect(created.stdout).toContain('repository old-field created at revision 1');
  expect((await api.get('/api/repositories/old-field')).body.credential_slug).not.toBeNull();
});
