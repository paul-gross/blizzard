import type { MockChunkSpec } from './client/mock';

// The priced OpenCode world: worker turns run under a subscription-billed `mock-opencode` (every step reports `cost: 0`)
// and are priced off a `models.json` in the host's cache home, so each turn carries an estimate and no billed cost.

const PROVIDER = 'acme';
const MODEL = 'coder-1';
const POOL = 'priced-pool';
const JUDGEMENT = "verdict('pass', 'the mock harness finished; checks are green')\n";

/** `mock-opencode`'s subscription-billed switch. */
export const BILLED_ZERO_VAR = 'BLIZZARD_MOCK_OPENCODE_BILLED_ZERO';

/** Per-token rates dear enough that any step's estimate is well above zero. */
export const PRICES = { [PROVIDER]: { [MODEL]: { input: 1000, output: 1000, cache_read: 1000, cache_write: 1000 } } };

function pricedNode(to: string, description: string): MockChunkSpec['nodes'][string] {
  return {
    executor: 'runner',
    session: 'resume',
    session_name: POOL,
    session_harnesses: ['opencode'],
    session_model: [`${PROVIDER}/${MODEL}`],
    judged_by: 'worker',
    prompt: 'pass\n',
    judgement_prompt: JUDGEMENT,
    choices: [{ name: 'pass', description, to }],
    retries_max: 1,
  };
}

/** An OpenCode chunk under the priced model: one `build` node, or `build` then `review` with `review`. */
export function pricedChunk(options: { review?: boolean } = {}): MockChunkSpec {
  return {
    entry: 'build',
    nodes: options.review === false ? { build: pricedNode('done', 'done') } : { build: pricedNode('review', 'done'), review: pricedNode('done', 'ready to land') },
    work_refs: [{ source: 'mock', ref: 'blizzard/toy-api/issues/1' }],
  };
}

/** The profile of a runner that prices OpenCode turns from the cache home and passes the billed-zero switch to its workers. */
export function pricedProfile(settings: { cost?: Record<string, number>; throttle?: Record<string, unknown> }) {
  return {
    runner: [
      {
        name: 'runner-local',
        opencode: { enabled: true, binary: '${mockBin}/mock-opencode' },
        worker: { env_passthrough: ['BLIZZARD_MOCK_HARNESS_FENCE', 'BZ_TRANSCRIPTS_ROOT', BILLED_ZERO_VAR, 'XDG_CACHE_HOME'] },
        ...(settings.cost === undefined ? {} : { cost: settings.cost }),
        ...(settings.throttle === undefined ? {} : { throttle: settings.throttle }),
      },
    ],
  };
}
