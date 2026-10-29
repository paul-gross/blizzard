import { expect, runnerTest as test } from '../../harness/fixtures';

// What a live host's runner registers, read back from the mock hub: its `bindings` and the `subscription` it names
// beside `workspace_id`, `env_capacity`, `projects` and its roster; and a runner naming no subscription registers an
// empty roster, samples nothing, and its panel lists no subscription — nothing is synthesized in its place. Every wait
// is a completed step of the host; every read is a public surface.

const RUNNER = 'runner-local';
const WORKSPACE = 'workspace-local';

/** A runner with two agents binding `default` and `web` in the home workspace, which holds four environments. */
const BOUND = {
  workspace: [{ id: WORKSPACE, max_environments: 4n }],
  runner: [
    {
      name: RUNNER,
      agents: 2n,
      projects: [
        { project: 'default', workspace: WORKSPACE },
        { project: 'web', workspace: WORKSPACE },
      ],
    },
  ],
};

test('test_the_hub_reads_back_a_runners_bindings_and_subscription_beside_its_registration', async ({ mockHub, runnerHosts }) => {
  expect((await mockHub.seed.project({ slug: 'web', name: 'Web' })).status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: BOUND });

  // The named subscription is sampled: its credential-less attempt records a miss.
  expect((await host.stepHost()).outcome).toBe('completed');
  expect((await host.step()).outcome).toBe('completed');
  const sampled = (await host.api.subscriptions()).body.items.find((item) => item.slug === 'anthropic');
  expect(sampled?.ok).toBe(false);
  expect(sampled?.miss_reason).not.toBeNull();

  const view = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/runners/${host.runners[RUNNER]!.id}`);
  expect(view.status).toBe(200);
  const runner = view.body as {
    workspace_id: string;
    env_capacity: number;
    projects: { entry: string }[];
    bindings: { project: string; workspace_id: string; agents: number }[];
    agents: number;
    subscription: string | null;
    subscription_unrostered: boolean;
    subscriptions: { slug: string }[];
  };
  expect(runner.workspace_id).toBe(WORKSPACE);
  expect(runner.env_capacity).toBe(4);
  expect(runner.projects.map((project) => project.entry)).toEqual(['default', 'web']);
  expect(runner.bindings.map((binding) => [binding.project, binding.workspace_id, binding.agents])).toEqual([
    ['default', WORKSPACE, 2],
    ['web', WORKSPACE, 2],
  ]);
  expect(runner.agents).toBe(2);
  expect([runner.subscription, runner.subscription_unrostered]).toEqual(['anthropic', false]);
  expect(runner.subscriptions.map((subscription) => subscription.slug)).toEqual(['anthropic']);
});

test('test_a_runner_naming_no_subscription_registers_an_empty_roster_and_samples_nothing', async ({ mockHub, runnerHosts }) => {
  expect((await mockHub.seed.project({ slug: 'web', name: 'Web' })).status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: BOUND, runnerSubscribed: false });

  // Several completed passes, each a chance to sample.
  for (let pass = 0; pass < 3; pass++) {
    expect((await host.stepHost()).outcome).toBe('completed');
    expect((await host.step()).outcome).toBe('completed');
  }

  const view = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/runners/${host.runners[RUNNER]!.id}`);
  expect(view.status).toBe(200);
  const runner = view.body as { subscription: string | null; subscriptions: unknown[] };
  expect(runner.subscription).toBeNull();
  expect(runner.subscriptions).toEqual([]);

  const reports = (await mockHub.captured.read()).body.requests.filter((request) => request.method === 'POST' && request.path === '/api/fleet/host');
  expect(reports.length).toBeGreaterThanOrEqual(1);
  expect((reports.at(-1)!.body as { subscriptions: unknown[] }).subscriptions).toEqual([]);

  const panel = await host.api.subscriptions();
  expect(panel.status).toBe(200);
  expect(panel.body.items).toEqual([]);
});
