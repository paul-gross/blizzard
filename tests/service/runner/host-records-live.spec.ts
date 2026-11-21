import { expect, runnerTest as test } from '../../harness/fixtures';

// A running host takes its workspace and subscription records up at its next host pass, with no restart: the report
// it sends the mock hub gains a created workspace and subscription, and loses a retired one. Every write goes through
// the `blizzard-runner` CLI against the running host; every read is the report the mock hub captured.

interface HostReportBody {
  workspaces: { workspace_id: string }[];
  subscriptions: { slug: string }[];
}

const WORKSPACE = 'workspace-local';

test('test_a_created_or_retired_workspace_and_subscription_reach_the_host_report_without_a_restart', async ({ mockHub, runnerHosts }, testInfo) => {
  const host = await runnerHosts.create();

  async function latestReport(): Promise<HostReportBody> {
    expect((await host.stepHost()).outcome).toBe('completed');
    const reports = (await mockHub.captured.read()).body.requests.filter((request) => request.method === 'POST' && request.path === '/api/fleet/host');
    expect(reports.length).toBeGreaterThanOrEqual(1);
    return reports.at(-1)!.body as HostReportBody;
  }

  async function cli(args: string[]): Promise<void> {
    const result = await host.cli(args);
    expect(result.code, result.stderr).toBe(0);
  }

  const before = await latestReport();
  expect(before.workspaces.map((workspace) => workspace.workspace_id)).toEqual([WORKSPACE]);
  expect(before.subscriptions.map((subscription) => subscription.slug)).not.toContain('team-b');

  await cli(['workspace', 'create', 'ws-b', `root="${testInfo.outputPath('ws-b')}"`]);
  await cli(['subscription', 'create', 'team-b', 'name="Team B"', 'provider="anthropic"']);
  const created = await latestReport();
  expect(created.workspaces.map((workspace) => workspace.workspace_id)).toEqual([WORKSPACE, 'ws-b']);
  expect(created.subscriptions.map((subscription) => subscription.slug)).toContain('team-b');

  await cli(['workspace', 'retire', 'ws-b']);
  await cli(['subscription', 'retire', 'team-b']);
  const retired = await latestReport();
  expect(retired.workspaces.map((workspace) => workspace.workspace_id)).toEqual([WORKSPACE]);
  expect(retired.subscriptions.map((subscription) => subscription.slug)).not.toContain('team-b');
});
