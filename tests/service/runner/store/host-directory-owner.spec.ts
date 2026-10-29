import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, type RunnerHost, runnerTest as test } from '../../../harness/fixtures';

// A host directory has one graduated shape and one owner, proven on real `blizzard-runner` processes: `host` and
// `migrate` refuse a directory that has not graduated and leave it byte for byte; while a running host holds
// `data/host.lock` — or another process does — a second `host`, adoption, `migrate --down`, `store reset` and `init`'s
// set-aside each refuse naming the holder's pid, and while a pre-host daemon answers on `runner.sock` each refuses
// naming the socket; a plain upgrade runs beside a running host; and a store a configured `db_url` names is upgraded in
// place with `data/runner.db` untouched. The holder is a real host the subject's lock links to; the store is read only
// before a host starts or after it stops.

const PARENT = '20261011_1000_runner_scope';
const NO_HUB = 'http://127.0.0.1:9';
const CURRENT = /store: .* is at the revision this build expects/;

/** Every file under `root` by relative path with its content hash; the lock names its last holder's pid, so it is left out. */
function tree(root: string): Record<string, string> {
  const files: Record<string, string> = {};
  for (const entry of fs.readdirSync(root, { recursive: true, encoding: 'utf8' }).sort()) {
    const file = path.join(root, entry);
    if (!fs.statSync(file).isFile() || path.basename(file) === 'host.lock') continue;
    files[entry] = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }
  return files;
}

/** What a refused verb would change under a running host, whose own store churns as it ticks: the files present, and the host file and token bytes. */
function heldShape(root: string): {
  files: string[];
  hostFile: string;
  env: string;
} {
  return {
    files: Object.keys(tree(root)),
    hostFile: fs.readFileSync(path.join(root, 'blizzard-host.toml'), 'utf8'),
    env: fs.readFileSync(path.join(root, '.env'), 'utf8'),
  };
}

function said(done: { stdout: string; stderr: string }): string {
  return done.stdout + done.stderr;
}

/** A copy of the unstarted host's directory, holding its migrated store and no lock of its own. */
function copyOf(source: RunnerHost, into: string): string {
  fs.cpSync(source.dir, into, {
    recursive: true,
    filter: (from) => path.basename(from) !== 'host.lock',
  });
  return into;
}

/** Adoption's starting point: the host file beside the pre-host store it has yet to adopt. */
function asPreHost(dir: string): string {
  fs.renameSync(path.join(dir, 'data', 'host.db'), path.join(dir, 'data', 'runner.db'));
  return dir;
}

/** The store `runner.db` a pre-host runner left with no config beside it: what `init --allow-readd` sets aside. */
function leftStore(dir: string): string {
  fs.mkdirSync(path.join(dir, 'data'), { recursive: true });
  const store = new DatabaseSync(path.join(dir, 'data', 'runner.db'));
  store.exec('CREATE TABLE t (v TEXT)');
  store.close();
  return dir;
}

test('test_host_and_migrate_refuse_an_ungraduated_directory_naming_the_procedure_and_change_nothing', async ({ scratchDirs }) => {
  const root = scratchDirs.make('pre-host');
  fs.mkdirSync(path.join(root, 'data'));
  fs.writeFileSync(path.join(root, 'blizzard-runner.toml'), `db_url = "sqlite:///${root}/data/runner.db"\nname = "runner-local"\n`);
  const store = new DatabaseSync(path.join(root, 'data', 'runner.db'));
  store.exec('CREATE TABLE marker (v TEXT)');
  store.close();
  fs.writeFileSync(path.join(root, '.env'), 'BZ_HUB_TOKEN=tok_pre_host\n');
  const before = tree(root);

  for (const verb of [
    ['host', '--dir', root],
    ['host', '--dir', root, '--check'],
    ['migrate', '--dir', root],
  ]) {
    const refused = await scratchDirs.cli(verb);
    expect(refused.code, verb.join(' ')).not.toBe(0);
    expect(said(refused)).toContain('has not graduated');
    expect(said(refused)).toContain('docs/upgrade.md');
  }

  expect(tree(root)).toEqual(before);
  expect(fs.readdirSync(root).sort()).toEqual(['.env', 'blizzard-runner.toml', 'data']);
});

test('test_a_running_host_holds_its_directory_against_every_verb_that_moves_its_store_but_a_plain_upgrade', async ({ runnerHost }) => {
  const before = heldShape(runnerHost.dir);
  const held = `pid ${runnerHost.pid}`;

  const second = await runnerHost.cli(['host', '--dir', runnerHost.dir]);
  const down = await runnerHost.cli(['migrate', '--dir', runnerHost.dir, '--down', 'base']);
  const reset = await runnerHost.cli(['store', 'reset', runnerHost.dir]);
  for (const refused of [second, down, reset]) {
    expect(refused.code).not.toBe(0);
    expect(said(refused)).toContain(held);
  }
  expect(said(second)).not.toContain('serving blizzard-runner');

  const upgrade = await runnerHost.cli(['migrate', '--dir', runnerHost.dir]);
  expect(upgrade.code, said(upgrade)).toBe(0);
  expect((await runnerHost.api.runners()).status).toBe(200);
  expect(heldShape(runnerHost.dir)).toEqual(before);
});

test('test_adoption_set_aside_and_an_upgrade_from_the_parent_revision_under_another_holder', async ({
  runnerHost,
  runnerHosts,
  scratchDirs,
}) => {
  const source = await runnerHosts.create({ runnerStarted: false });
  const adopting = asPreHost(copyOf(source, scratchDirs.make('adopting')));
  const left = leftStore(scratchDirs.make('left'));
  const upgrading = copyOf(source, scratchDirs.make('upgrading'));
  const down = await scratchDirs.cli(['migrate', '--dir', upgrading, '--down', PARENT]);
  expect(down.code, said(down)).toBe(0);
  // Each subject's lock is the running host's own, so that host is the holder every refusal names.
  for (const dir of [adopting, left, upgrading]) {
    fs.mkdirSync(path.join(dir, 'data'), { recursive: true });
    fs.rmSync(path.join(dir, 'data', 'host.lock'), { force: true });
    fs.symlinkSync(path.join(runnerHost.dir, 'data', 'host.lock'), path.join(dir, 'data', 'host.lock'));
  }
  const adoptingBefore = tree(adopting);
  const leftBefore = tree(left);

  const adoption = await scratchDirs.cli(['migrate', '--dir', adopting]);
  const setAside = await scratchDirs.cli(['init', left, '--hub', NO_HUB, '--allow-readd']);
  const upgrade = await scratchDirs.cli(['migrate', '--dir', upgrading]);

  expect(adoption.code).not.toBe(0);
  expect(said(adoption)).toContain(`pid ${runnerHost.pid}`);
  expect(setAside.code).not.toBe(0);
  expect(said(setAside)).toContain(`pid ${runnerHost.pid}`);
  expect(upgrade.code, said(upgrade)).toBe(0);
  expect(tree(adopting)).toEqual(adoptingBefore);
  expect(tree(left)).toEqual(leftBefore);
  expect(said(await scratchDirs.cli(['host', '--dir', upgrading, '--check']))).toMatch(CURRENT);
});

test('test_every_verb_that_moves_the_store_refuses_while_a_pre_host_daemon_answers_naming_its_socket', async ({
  runnerHosts,
  scratchDirs,
  preHostDaemon,
}) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  const adopting = asPreHost(copyOf(host, scratchDirs.make('adopting')));
  const left = leftStore(scratchDirs.make('left'));
  const hostSock = await preHostDaemon(host.dir);
  const adoptingSock = await preHostDaemon(adopting);
  const leftSock = await preHostDaemon(left);
  const roots = [host.dir, adopting, left];
  const trees = roots.map(tree);

  const refusals: Record<string, [Awaited<ReturnType<typeof host.cli>>, string]> = {
    host: [await host.cli(['host', '--dir', host.dir]), hostSock],
    'migrate --down': [await host.cli(['migrate', '--dir', host.dir, '--down', 'base']), hostSock],
    'store reset': [await host.cli(['store', 'reset', host.dir]), hostSock],
    upgrade: [await host.cli(['migrate', '--dir', host.dir]), hostSock],
    adoption: [await scratchDirs.cli(['migrate', '--dir', adopting]), adoptingSock],
    'init set-aside': [await scratchDirs.cli(['init', left, '--hub', NO_HUB, '--allow-readd']), leftSock],
  };

  for (const [verb, [refused, socket]] of Object.entries(refusals)) {
    expect(refused.code, verb).not.toBe(0);
    expect(said(refused), verb).toContain(socket);
  }
  expect(roots.map(tree)).toEqual(trees);
});

test('test_a_store_a_configured_db_url_names_is_upgraded_in_place_leaving_data_runner_db_untouched', async ({
  runnerHosts,
  scratchDirs,
}) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  const down = await host.cli(['migrate', '--dir', host.dir, '--down', PARENT]);
  expect(down.code, said(down)).toBe(0);
  const named = path.join(scratchDirs.make('elsewhere'), 'named.db');
  const store = path.join(host.dir, 'data', 'host.db');
  const legacy = path.join(host.dir, 'data', 'runner.db');
  fs.renameSync(store, named);
  fs.copyFileSync(named, legacy);
  const hostFile = path.join(host.dir, 'blizzard-host.toml');
  fs.writeFileSync(hostFile, `db_url = "sqlite:///${named}"\n${fs.readFileSync(hostFile, 'utf8')}`);
  const before = fs.readFileSync(legacy);

  const upgraded = await host.cli(['migrate', '--dir', host.dir]);

  expect(upgraded.code, said(upgraded)).toBe(0);
  expect(fs.readFileSync(legacy).equals(before)).toBe(true);
  expect(fs.existsSync(store)).toBe(false);
  expect(said(await host.cli(['host', '--dir', host.dir, '--check']))).toMatch(CURRENT);
});
