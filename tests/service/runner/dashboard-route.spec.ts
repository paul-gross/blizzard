import { expect, holdingChunk, type MockChunkSpec, seededLeaseWithWorker, stepUntil, runnerTest as test } from '../../harness/fixtures';

// A real runner host stepped against the test's own mock hub: the dashboard the runner composes equals each read it
// composes from, with every section populated by a real flow. The host is paused once the state is built, so the
// compared reads see no pass between them.

const RUNNER = 'runner-local';

/** A chunk whose judgement crashes with no retry: the runner escalates on its first attempt. */
const ESCALATING: MockChunkSpec = {
  ...holdingChunk(),
  nodes: { build: { ...holdingChunk().nodes.build!, judgement_prompt: 'crash()\n', retries_max: 0 } },
};

test('test_dashboard_matches_the_individual_reads_over_a_real_running_daemon', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;

  // An escalation first, while the runner has a free slot. Its closed lease keeps its session, so a person takes it over without force.
  const escalated = await mockHub.seed.chunk(ESCALATING);
  expect(escalated.status).toBe(201);
  await stepUntil(
    runnerHost,
    async () => (await runner.escalations()).body.items.filter((item) => item.chunk_id === escalated.body.chunk_id),
    (items) => items.length > 0,
  );
  expect((await runner.openTakeover(escalated.body.chunk_id)).status).toBe(201);

  // Then an open ask, put as the worker of a live lease.
  const { leaseId, headers } = await seededLeaseWithWorker(mockHub, runnerHost, holdingChunk());
  expect((await runner.ask(leaseId, 'which branch?', ['main', 'dev'], headers)).status).toBe(201);

  // Paused, so no pass changes state between the reads below.
  expect((await runner.setPaused(true, 'dashboard-spec')).status).toBe(200);

  const compare = async () => {
    const dashboard = await runner.dashboard();
    expect(dashboard.status).toBe(200);
    const [status, asks, escalations, takeovers, facts, harnessHealth] = await Promise.all([
      runner.status(),
      runner.asks(),
      runner.escalations(),
      runner.takeovers(),
      runner.facts(),
      runner.harnessHealth(),
    ]);
    expect(dashboard.body.runner).toEqual(status.body);
    expect(dashboard.body.asks).toEqual(asks.body);
    expect(dashboard.body.escalations).toEqual(escalations.body);
    expect(dashboard.body.takeovers).toEqual(takeovers.body);
    expect(dashboard.body.facts).toEqual(facts.body);
    expect(dashboard.body.harness_health).toEqual(harnessHealth.body);
    return dashboard.body;
  };

  // Real data round-tripped, not empty bodies equal to each other.
  const body = await compare();
  expect(body.runner.pause.local).toBe(true);
  expect(body.asks.items.length).toBeGreaterThan(0);
  expect(body.escalations.items.length).toBeGreaterThan(0);
  expect(body.takeovers.items.length).toBeGreaterThan(0);
  expect(body.facts.items.length).toBeGreaterThan(0);

  // The mock hub serves no fleet summary, so that slot degrades to null rather than failing the read.
  expect(body.fleet_summary).toBeNull();

  // A hub outage is a second, distinct failure of the same slot; the local sections stay populated.
  await mockHub.levers.arm('unreachable', { remaining: 10_000 });
  const degraded = await compare();
  expect(degraded.fleet_summary).toBeNull();
  expect(degraded.asks.items.length).toBeGreaterThan(0);
});
