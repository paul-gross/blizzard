import { expect, runnerTest as test } from '../../harness/fixtures';

// A runner added to a registered host with no hub session, and retired from it, on real `blizzard-runner` processes and a
// test-owned mock hub: `runner add` holds no session, yet the mock holds the runner under the host and the host runs it
// after a restart; `runner retire` halts it on the running host at its next tick and removes its entry, after which
// `init` runs again and a restarted host serves the runner that remains. Every wait is a completed step.

test('test_runner_add_with_no_session_runs_after_a_restart_and_retire_halts_it_within_a_tick', async ({ mockHub, runnerHost }) => {
  // Each CLI verb is a fresh Python process; a loaded machine puts the run past the default timeout.
  test.slow();
  const first = runnerHost.runners['runner-local']!;
  const init = ['init', runnerHost.dir, '--hub', mockHub.url];

  const added = await runnerHost.cli(['add', 'r2', '--project', 'default']);
  expect(added.code, added.stdout + added.stderr).toBe(0);
  expect(added.stdout).toContain('it starts runner r2 at its next start');
  const record = await mockHub.fleetOf(runnerHost).get('/api/fleet/host');
  expect(record.status).toBe(200);
  const ids = (record.body.runners as { runner_id: string; name?: string }[]).map((runner) => runner.runner_id);
  expect(ids).toHaveLength(2);
  expect(ids).toContain(first.id);
  const r2 = ids.find((id) => id !== first.id)!;

  // `init` run again writes r2's identity row, so the retire below leaves a store its host still starts.
  await runnerHost.stop();
  const rejoined = await runnerHost.cli(init);
  expect(rejoined.code, rejoined.stdout + rejoined.stderr).toBe(0);
  await runnerHost.restart();
  expect((await runnerHost.socketApi.stepRunner(r2)).outcome).toBe('completed');
  expect((await runnerHost.api.runner(r2).status()).body.identity?.state).toBe('matched');
  expect((await runnerHost.step()).outcome).toBe('completed');

  const retired = await runnerHost.cli(['retire', '--runner', 'r2']);
  expect(retired.code, retired.stdout + retired.stderr).toBe(0);
  expect(retired.stdout).toContain('it halts runner r2 at its next tick');
  expect((await runnerHost.stepHost()).outcome).toBe('completed');
  const listed = (await runnerHost.api.runners()).body.items;
  expect(listed.find((item) => item.runner_id === r2)?.state).toBe('halted');
  expect((await first.status()).body.identity?.state).toBe('matched');

  await runnerHost.stop();
  const again = await runnerHost.cli(init);
  expect(again.code, again.stdout + again.stderr).toBe(0);
  await runnerHost.restart();
  expect((await runnerHost.step()).outcome).toBe('completed');
  expect((await first.status()).body.identity?.state).toBe('matched');
  expect((await runnerHost.api.runners()).body.items.map((item) => item.runner_id)).not.toContain(r2);
});
