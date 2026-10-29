import { expect, runnerTest as test } from '../../harness/fixtures';

// A local verb addresses the host's runner by `--runner`: a live host of one runner answers `status` with `--runner`
// omitted, named, or given its id, and refuses an unmatched `--runner` naming the runners it holds.

const RUNNER = 'runner-local';

test('test_status_addresses_a_host_of_ones_runner_unnamed_by_name_or_by_id_and_refuses_any_other', async ({ runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;

  for (const selector of [[], ['--runner', runner.name], ['--runner', runner.id]]) {
    const selected = await runnerHost.cli(['status', ...selector]);
    expect(selected.code, selector.join(' ') + selected.stdout + selected.stderr).toBe(0);
    expect(selected.stdout, selector.join(' ')).toContain(runner.name);
  }

  const unmatched = await runnerHost.cli(['status', '--runner', 'nope']);
  expect(unmatched.code).not.toBe(0);
  const said = unmatched.stdout + unmatched.stderr;
  expect(said).toContain("holds no runner 'nope'");
  expect(said).toContain(`${runner.name} (${runner.id}`);
});
