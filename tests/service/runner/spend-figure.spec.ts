import { expect, runnerTest as test } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// The one dollar figure on a stepped runner host: OpenCode worker turns run under a subscription-billed `mock-opencode`
// (every step reports `cost: 0`) and are priced off a `models.json` in the host's cache home. Both spend controls compare
// and render that estimate as the one `~`-led figure: the runner ceiling's local-pause reason, read off the runner's
// status, and the chunk cap's spend-cap escalation, read off the mock hub.

const MAX_STEPS = 600;
const PROVIDER = 'acme';
const MODEL = 'coder-1';
// `mock-opencode`'s subscription-billed switch.
const BILLED_ZERO_VAR = 'BLIZZARD_MOCK_OPENCODE_BILLED_ZERO';

test.setTimeout(90_000);

const JUDGEMENT = "verdict('pass', 'the mock harness finished; checks are green')\n";

const POOL = 'priced-pool';

/** A build then review OpenCode chunk under the priced model, whose worker turns each carry an estimate and no billed cost. */
const pricedChunk: MockChunkSpec = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      session_model: [`${PROVIDER}/${MODEL}`],
      judged_by: 'worker',
      prompt: 'pass\n',
      judgement_prompt: JUDGEMENT,
      choices: [{ name: 'pass', description: 'done', to: 'review' }],
      retries_max: 1,
    },
    review: {
      executor: 'runner',
      session: 'resume',
      session_name: POOL,
      session_harnesses: ['opencode'],
      session_model: [`${PROVIDER}/${MODEL}`],
      judged_by: 'worker',
      prompt: 'pass\n',
      judgement_prompt: JUDGEMENT,
      choices: [{ name: 'pass', description: 'ready to land', to: 'done' }],
      retries_max: 1,
    },
  },
  work_refs: [{ source: 'mock', ref: 'blizzard/toy-api/issues/1' }],
};

/** Per-token rates dear enough that any step's estimate is well above zero. */
const PRICES = { [PROVIDER]: { [MODEL]: { input: 1000, output: 1000, cache_read: 1000, cache_write: 1000 } } };

/** The profile of a runner that prices OpenCode turns from the cache home and passes the billed-zero switch to its workers. */
function pricedProfile(cost: Record<string, number>) {
  return {
    runner: [
      {
        name: 'runner-local',
        opencode: { enabled: true, binary: '${mockBin}/mock-opencode' },
        worker: { env_passthrough: ['BLIZZARD_MOCK_HARNESS_FENCE', 'BZ_TRANSCRIPTS_ROOT', BILLED_ZERO_VAR, 'XDG_CACHE_HOME'] },
        cost,
      },
    ],
  };
}

/** Steps the host until `read` reaches a state `reached` accepts, failing after `MAX_STEPS` passes with the last state it saw. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let pass = 0; !reached(state); pass++) {
    if (pass === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    await host.step();
    state = await read();
  }
  return state;
}

async function chunkEscalation(mockHub: MockHub, chunkId: string) {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  return async () => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body.escalation as { cause: string; detail: string | null } | null;
}

test('test_runner_ceiling_pauses_on_an_estimated_spend_with_a_tilde_led_reason', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerConfig: pricedProfile({ runner_ceiling_usd: 0.0001 }),
    runnerEnv: { [BILLED_ZERO_VAR]: '1' },
    runnerModelPrices: PRICES,
  });
  await mockHub.seed.chunk(pricedChunk);
  const runner = host.runners['runner-local']!;

  // Billed-zero steps carry only an estimate; the ceiling, set under it, still engages the local pause.
  const pause = await stepUntil(host, async () => (await runner.status()).body.pause, (state) => state.local);
  expect(pause.local_reason).toContain('spend ceiling');
  expect(pause.local_reason, 'the estimate-carrying spend did not render as the one ~-led figure').toContain('~$');
});

test('test_chunk_cap_escalates_on_an_estimated_chunk_cost_with_a_tilde_marked_detail', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({
    runnerConfig: pricedProfile({ chunk_cap_usd: 1 }),
    runnerEnv: { [BILLED_ZERO_VAR]: '1' },
    runnerModelPrices: PRICES,
  });
  // The hub's derived chunk total carries only an estimate, past the cap.
  const seeded = await mockHub.seed.chunk({ ...pricedChunk, cost: { cost_usd: 0, estimated_cost_usd: 5 } });
  const escalation = await chunkEscalation(mockHub, seeded.body.chunk_id);

  const raised = await stepUntil(host, escalation, (state) => state !== null && state !== undefined);
  expect(raised!.cause).toBe('spend-cap');
  expect(raised!.detail).toContain('~$5.00');
});
