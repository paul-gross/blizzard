import { BILLED_ZERO_VAR, expect, PRICES, pricedChunk as pricedChunkOf, pricedProfile, runnerTest as test, stepUntil } from '../../harness/fixtures';
import type { MockHub } from '../../harness/fixtures';

// The one dollar figure on a stepped runner host: OpenCode worker turns run under a subscription-billed `mock-opencode`
// (every step reports `cost: 0`) and are priced off a `models.json` in the host's cache home. Both spend controls compare
// and render that estimate as the one `~`-led figure: the runner ceiling's local-pause reason, read off the runner's
// status, and the chunk cap's spend-cap escalation, read off the mock hub.

const MAX_STEPS = 600;
const pricedChunk = pricedChunkOf();

test.setTimeout(90_000);

async function chunkEscalation(mockHub: MockHub, chunkId: string) {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  return async () => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.escalation as { cause: string; detail: string | null } | null;
}

test('test_runner_ceiling_pauses_on_an_estimated_spend_with_a_tilde_led_reason', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerConfig: pricedProfile({ cost: { runner_ceiling_usd: 0.0001 } }),
    runnerEnv: { [BILLED_ZERO_VAR]: '1' },
    runnerModelPrices: PRICES,
  });
  await mockHub.seed.chunk(pricedChunk);
  const runner = host.runners['runner-local']!;

  // Billed-zero steps carry only an estimate; the ceiling, set under it, still engages the local pause.
  const pause = await stepUntil(host, async () => (await runner.status()).body.pause, (state) => state.local, MAX_STEPS);
  expect(pause.local_reason).toContain('spend ceiling');
  expect(pause.local_reason, 'the estimate-carrying spend did not render as the one ~-led figure').toContain('~$');
});

test('test_chunk_cap_escalates_on_an_estimated_chunk_cost_with_a_tilde_marked_detail', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerConfig: pricedProfile({ cost: { chunk_cap_usd: 1 } }),
    runnerEnv: { [BILLED_ZERO_VAR]: '1' },
    runnerModelPrices: PRICES,
  });
  // The hub's derived chunk total carries only an estimate, past the cap.
  const seeded = await mockHub.seed.chunk({ ...pricedChunk, cost: { cost_usd: 0, estimated_cost_usd: 5 } });
  const escalation = await chunkEscalation(mockHub, seeded.body.chunk_id);

  const raised = await stepUntil(host, escalation, (state) => state !== null && state !== undefined, MAX_STEPS);
  expect(raised!.cause).toBe('spend-cap');
  expect(raised!.detail).toContain('~$5.00');
});
