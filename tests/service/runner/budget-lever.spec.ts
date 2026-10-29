import { BILLED_ZERO_VAR, expect, PRICES, pricedChunk, pricedProfile, runnerTest as test, stepUntil } from '../../harness/fixtures';
import type { RunnerHost, RunnerHosts } from '../../harness/fixtures';

// A dollar budget throttles a runner below its ceiling: one stepped host, one runner spending a priced worker's estimate
// and configuring a budget. A runner whose spend stands at its budget is declined, so the hub shows it declining on
// `budget` and no lease is minted, and it carries on as that spend ages out of the trailing window; a runner that spends
// through its budget to its ceiling trips the ceiling's local pause, which stays on after the spend has aged out.

const RUNNER = 'runner-local';
// A priced worker turn estimates about $0.8, and a build spends two (spawn and judgement): both thresholds sit under that total, the budget the lower.
const BUDGET_USD = 0.5;
const CEILING_USD = 1;
// Seconds the trailing window spans; spend ages out of it only by the clock, so the expiry is awaited on the runner's own read.
const WINDOW_SECONDS = 30;
const WINDOW_HOURS = WINDOW_SECONDS / 3600;
const PASSES = 600;

test.setTimeout(180_000);

function pricedHost(runnerHosts: RunnerHosts, withCeiling: boolean) {
  return runnerHosts.create({
    runnerConfig: pricedProfile({
      throttle: { budget: { usd: BUDGET_USD, window_hours: WINDOW_HOURS } },
      cost: withCeiling ? { runner_ceiling_usd: CEILING_USD, window_hours: WINDOW_HOURS } : undefined,
    }),
    runnerEnv: { [BILLED_ZERO_VAR]: '1' },
    runnerModelPrices: PRICES,
    runnerSampling: true,
  });
}

/** Polls the runner's own throttle read until the budget lever admits: the spend has aged out of the window. */
async function budgetAdmits(host: RunnerHost): Promise<void> {
  const runner = host.runners[RUNNER]!;
  await expect
    .poll(async () => (await runner.status()).body.throttle?.levers.find((lever) => lever.name === 'budget')?.admits, {
      message: host.logTail(),
      timeout: (WINDOW_SECONDS + 30) * 1000,
    })
    .toBe(true);
}

test('test_a_runner_at_its_budget_waits_for_its_spend_to_age_out_then_carries_on', async ({ mockHub, runnerHosts }) => {
  const host = await pricedHost(runnerHosts, false);
  const runner = host.runners[RUNNER]!;
  const fleet = mockHub.fleetOf(host, RUNNER);
  const declining = async () => ((await fleet.get(`/api/fleet/runners/${runner.runnerId}`)).body.declining as { lever: string }[]).map((entry) => entry.lever);

  // The sampled subscription the runner spends.
  await host.stepHost();
  expect((await host.api.subscriptions()).body.items.find((item) => item.slug === 'anthropic')).toBeDefined();

  // A first chunk spends past the budget.
  const first = (await mockHub.seed.chunk(pricedChunk({ review: false }))).body.chunk_id;
  const spent = await stepUntil(
    host,
    async () => (await runner.leases()).body.items.find((item) => item.chunk_id === first),
    (lease) => lease !== undefined,
    PASSES,
  );
  await stepUntil(host, async () => (await runner.leaseUsage(spent!.lease_id)).body.items.length, (count) => count > 0, PASSES);

  // The spend stands inside the window: the runner is declined on the budget and mints no lease.
  const second = (await mockHub.seed.chunk(pricedChunk({ review: false }))).body.chunk_id;
  await stepUntil(host, declining, (levers) => levers.length === 1 && levers[0] === 'budget', PASSES);
  expect((await runner.leases()).body.items.filter((item) => item.chunk_id === second)).toEqual([]);

  // The spend ages out of the window: the budget admits again and the runner carries on.
  await budgetAdmits(host);
  await stepUntil(host, async () => (await runner.leases()).body.items.some((item) => item.chunk_id === second), Boolean, PASSES);
  await stepUntil(host, declining, (levers) => levers.length === 0, PASSES);
});

test('test_a_runner_spending_through_its_budget_trips_its_ceiling_and_stays_tripped', async ({ mockHub, runnerHosts }) => {
  const host = await pricedHost(runnerHosts, true);
  const runner = host.runners[RUNNER]!;
  const leasesOf = async (node: string) => (await runner.leases()).body.items.filter((item) => item.node_name === node);

  await host.stepHost();
  expect((await host.api.subscriptions()).body.items.find((item) => item.slug === 'anthropic')).toBeDefined();

  await mockHub.seed.chunk(pricedChunk());
  // The ceiling is judged at the start of a pass, before that pass's worker has spent: the first lease is unpaused.
  await stepUntil(host, async () => (await leasesOf('build')).length, (count) => count > 0, PASSES);
  expect((await runner.status()).body.pause.local).toBe(false);

  // The admitted step spends through the budget and on to the ceiling: the ceiling trips.
  const pause = await stepUntil(host, async () => (await runner.status()).body.pause, (state) => state.local, PASSES);
  expect(pause.local_reason).toContain('spend ceiling');

  // The spend ages out of the window, which would admit the budget again; the ceiling stays tripped.
  await budgetAdmits(host);
  await host.step();
  const status = (await runner.status()).body;
  expect(status.pause.local).toBe(true);
  expect(status.throttle?.state).toBe('braked');
  expect(await leasesOf('review')).toEqual([]);
  expect(await leasesOf('build')).toHaveLength(1);
});
