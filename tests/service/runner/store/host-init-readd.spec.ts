import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { parse } from 'smol-toml';
import { expect, runnerTest as test } from '../../../harness/fixtures';

// `init --allow-readd` over what a pre-host runner left behind, on a real `blizzard-runner` and a test-owned mock hub: a
// directory holding a pre-host `data/runner.db` and its token under the unsuffixed variable, with no config beside them.
// Without the flag `init` refuses, names graduation, and changes nothing; with it the store is set aside, a runner is
// added at the mock hub, its id lands in the host file and its token under its own variable, and a host then starts over
// the directory and finds its identity matched at the hub. The store is read only while no host runs.

const PARENT = '20261011_1000_runner_scope';
const PRE_HOST_RUNNER_ID = 'rn_01JX000000000000000000LEFT';
const PRE_HOST_TOKEN = 'tok_pre_host';

/** Every file under `root` by relative path with its content hash. */
function tree(root: string): Record<string, string> {
  const files: Record<string, string> = {};
  for (const entry of fs.readdirSync(root, { recursive: true, encoding: 'utf8' }).sort()) {
    const file = path.join(root, entry);
    if (!fs.statSync(file).isFile()) continue;
    files[entry] = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }
  return files;
}

/** A pre-host runner's store at `data/runner.db` — its identity row naming the pre-host runner — and its token under the unsuffixed variable. */
function leftBehind(dir: string): void {
  fs.mkdirSync(path.join(dir, 'data'), { recursive: true });
  const store = new DatabaseSync(path.join(dir, 'data', 'runner.db'));
  store.exec(`CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)`);
  store.exec(`INSERT INTO alembic_version VALUES ('${PARENT}')`);
  store.exec(`CREATE TABLE runner_identity (
    id INTEGER NOT NULL PRIMARY KEY,
    runner_id VARCHAR NOT NULL,
    runner_name VARCHAR NOT NULL,
    registered_at DATETIME NOT NULL,
    tenant_id VARCHAR,
    tenant_name VARCHAR,
    projects_answered_at DATETIME
  )`);
  store
    .prepare('INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (?, ?, ?)')
    .run(PRE_HOST_RUNNER_ID, 'runner-local', new Date().toISOString());
  store.close();
  fs.writeFileSync(path.join(dir, '.env'), `BZ_HUB_TOKEN=${PRE_HOST_TOKEN}\n`);
}

/** The runner ids the set-aside store at `file` still names. */
function identityIds(file: string): string[] {
  const store = new DatabaseSync(file, { readOnly: true });
  try {
    return (store.prepare('SELECT runner_id FROM runner_identity').all() as { runner_id: string }[]).map((row) => row.runner_id);
  } finally {
    store.close();
  }
}

test('test_init_with_allow_readd_sets_a_pre_host_store_aside_adds_a_runner_and_the_host_starts', async ({ mockHub, scratchDirs, runnerHosts }) => {
  // Each CLI verb is a fresh Python process; a loaded machine puts the run past the default timeout.
  test.slow();
  const dir = scratchDirs.make('left');
  leftBehind(dir);
  const before = tree(dir);

  const refused = await scratchDirs.cli(['init', dir, '--hub', mockHub.url]);
  const said = refused.stdout + refused.stderr;
  expect(refused.code, said).not.toBe(0);
  expect(said).toContain('graduation procedure in docs/upgrade.md');
  expect(said).toContain('--allow-readd');
  expect(tree(dir)).toEqual(before);

  const readded = await scratchDirs.cli(['init', dir, '--hub', mockHub.url, '--allow-readd']);
  expect(readded.code, readded.stdout + readded.stderr).toBe(0);

  expect(fs.existsSync(path.join(dir, 'data', 'runner.db'))).toBe(false);
  const asides = fs.readdirSync(path.join(dir, 'data')).filter((name) => name.startsWith('runner.db.'));
  expect(asides).toHaveLength(1);
  expect(identityIds(path.join(dir, 'data', asides[0]!))).toEqual([PRE_HOST_RUNNER_ID]);

  const file = parse(fs.readFileSync(path.join(dir, 'blizzard-host.toml'), 'utf8')) as { runner: { id: string; name: string }[] };
  const added = file.runner[0]!;
  expect(added.id).toMatch(/^rn_/);
  expect(added.id).not.toBe(PRE_HOST_RUNNER_ID);
  const variable = `BZ_HUB_TOKEN_${added.name.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`;
  expect(fs.readFileSync(path.join(dir, '.env'), 'utf8')).toMatch(new RegExp(`^${variable}=.+$`, 'm'));

  const host = await runnerHosts.create({ runnerDir: dir });
  const runner = host.runners[added.name]!;
  expect(runner.id).toBe(added.id);
  const token = host.hubTokens().runners[added.name]!;
  const identity = await mockHub.fleet(token).get('/api/fleet/identity');
  expect(identity.status).toBe(200);
  expect(identity.body.runner_id).toBe(added.id);

  expect((await host.step()).outcome).toBe('completed');
  const status = await runner.status();
  expect(status.body.runner_id).toBe(added.id);
  expect(status.body.identity?.state).toBe('matched');
  expect((await mockHub.fleet(token).get(`/api/fleet/runners/${added.id}`)).status).toBe(200);
});
