import type { FactsDetail, FactsNeighbor } from './chunk-facts';

/** Presentation inputs, independent of the generated response and its projection. */
export function factsNeighbor(overrides: Partial<FactsNeighbor> = {}): FactsNeighbor {
  return { chunk_id: 'ch_01neighbor00000000000000000', laneStatus: { kind: 'done' }, ...overrides };
}

export function factsDetail(overrides: Partial<FactsDetail> = {}): FactsDetail {
  return {
    cost: { input_tokens: 42_000, output_tokens: 18_500, cache_read_tokens: 1_250_000, cache_create_tokens: 96_000, cost_usd: 3.42, cost_partial: false, estimated_cost_usd: null },
    neighborhood: { prerequisites: [], dependents: [] },
    ...overrides,
  };
}
