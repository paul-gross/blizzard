import type { ChunkCountsView, FleetSpendView } from '../../api/hub';
import { fixtureInstant } from './clock';

/** The board's chunk counts: a fleet mid-day, `total` summing the rest. */
export function chunkCountsView(overrides: Partial<ChunkCountsView> = {}): ChunkCountsView {
  return {
    not_ready: 2,
    ready: 3,
    running: 4,
    delivering: 1,
    needs_human: 1,
    waiting_on_human: 0,
    paused: 0,
    stopped: 0,
    done: 12,
    terminal: 12,
    total: 23,
    ...overrides,
  };
}

/** The fleet's spend over the day ending at {@link FIXTURE_NOW}. */
export function fleetSpendView(overrides: Partial<FleetSpendView> = {}): FleetSpendView {
  return {
    since: fixtureInstant(-24 * 3600),
    until: fixtureInstant(0),
    input_tokens: 310_000,
    output_tokens: 120_000,
    cache_read_tokens: 9_400_000,
    cache_create_tokens: 640_000,
    cost_usd: 27.5,
    cost_partial: false,
    ...overrides,
  };
}
