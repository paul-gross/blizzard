import * as fs from 'node:fs';
import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, runnerTest as test } from '../../../harness/fixtures';

// The subject is what a freshly initialized runner host looks like, so `init` runs for real.
test.use({ coldInit: true });

test('a newly added runner serves settings after its first tick without reinitializing', async ({ mockHub, runnerHost }) => {
  test.slow();
  const first = runnerHost.runners['runner-local']!;
  const hostFile = runnerHost.hostFile();
  const added = await runnerHost.cli(['add', 'r2', '--project', 'default']);
  expect(added.code, added.stdout + added.stderr).toBe(0);
  expect(added.stdout).toContain('through the running host');
  const record = await mockHub.fleetOf(runnerHost).get('/api/fleet/host');
  expect(record.status).toBe(200);
  const runners = record.body.runners as { runner_id: string }[];
  const r2 = runners.find((runner) => runner.runner_id !== first.id)!.runner_id;

  expect((await runnerHost.stepHost()).outcome).toBe('completed');
  expect((await runnerHost.socketApi.stepRunner(r2)).outcome).toBe('completed');
  const settings = await runnerHost.api.runner(r2).settings();
  expect(settings.status).toBe(200);
  expect(settings.body.settings.find((row) => row.key === 'projects')).toMatchObject({
    value: [{ project: 'default', workspace: 'workspace-local' }],
    source: 'entry',
    written_by: 'operator',
  });
  expect(runnerHost.hostFile()).toBe(hostFile);
  expect(fs.existsSync(path.join(runnerHost.dir, 'runners', 'r2'))).toBe(false);
});

// A runner added to a registered, running host with no hub session, and retired from it, on real `blizzard-runner`
// processes and a test-owned mock hub: `runner add` holds no session, yet the mock holds the runner under the host, the
// host starts it at its next tick and runs it after a restart; `runner retire` drains it at the host's next tick and
// keeps its identity, which `init` accepts as the host's retired runner and `store reset --runner` archives.

test('test_runner_add_with_no_session_runs_after_a_restart_and_retire_drains_it_within_a_tick', async ({ mockHub, runnerHost }) => {
  // Each CLI verb is a fresh Python process; a loaded machine puts the run past the default timeout.
  test.slow();
  const first = runnerHost.runners['runner-local']!;
  const init = ['init', runnerHost.dir, '--hub', mockHub.url];

  const added = await runnerHost.cli(['add', 'r2', '--project', 'default']);
  expect(added.code, added.stdout + added.stderr).toBe(0);
  expect(added.stdout).toContain('it starts the runner at its next tick');
  const record = await mockHub.fleetOf(runnerHost).get('/api/fleet/host');
  expect(record.status).toBe(200);
  const ids = (record.body.runners as { runner_id: string; name?: string }[]).map((runner) => runner.runner_id);
  expect(ids).toHaveLength(2);
  expect(ids).toContain(first.id);
  const r2 = ids.find((id) => id !== first.id)!;

  await runnerHost.restart();
  expect((await runnerHost.socketApi.stepRunner(r2)).outcome).toBe('completed');
  expect((await runnerHost.api.runner(r2).status()).body.identity?.state).toBe('matched');
  expect((await runnerHost.step()).outcome).toBe('completed');

  const retired = await runnerHost.cli(['retire', '--runner', 'r2']);
  expect(retired.code, retired.stdout + retired.stderr).toBe(0);
  expect(retired.stdout).toContain('it drains the runner at its next tick');
  expect((await runnerHost.stepHost()).outcome).toBe('completed');
  expect((await runnerHost.api.runners()).body.items.map((item) => item.runner_id)).not.toContain(r2);
  expect((await first.status()).body.identity?.state).toBe('matched');

  await runnerHost.stop();
  const rejoined = await runnerHost.cli(init);
  expect(rejoined.code, rejoined.stdout + rejoined.stderr).toBe(0);
  const archived = await runnerHost.cli(['store', 'reset', runnerHost.dir, '--runner', r2]);
  expect(archived.code, archived.stdout + archived.stderr).toBe(0);
  const dataDir = path.join(runnerHost.dir, 'data');
  const archives = fs.readdirSync(dataDir).filter((name) => name.startsWith(`host.db.${r2}.`));
  expect(archives).toHaveLength(1);
  const archive = new DatabaseSync(path.join(dataDir, archives[0]!), { readOnly: true });
  try {
    expect(archive.prepare('SELECT runner_id FROM runner_identity').all().map((row) => row.runner_id)).toEqual([r2]);
    expect(archive.prepare('SELECT runner_id FROM runner_settings').all().map((row) => row.runner_id)).toEqual([r2]);
  } finally {
    archive.close();
  }
  const again = await runnerHost.cli(init);
  expect(again.code, again.stdout + again.stderr).toBe(0);
  await runnerHost.restart();
  expect((await runnerHost.step()).outcome).toBe('completed');
  expect((await first.status()).body.identity?.state).toBe('matched');
  expect((await runnerHost.api.runners()).body.items.map((item) => item.runner_id)).not.toContain(r2);
});
