import { describe, expect, it } from 'vitest';
import { fleetSpendView } from 'fleet/testing';

import { fromFleetSpendView } from './fleet-spend.translate';

describe('fromFleetSpendView', () => {
  it('carries cost and token usage', () => {
    expect(fromFleetSpendView(fleetSpendView({ cost_usd: 3, estimated_cost_usd: 0.25, cost_partial: true }))).toEqual({
      cost_usd: 3,
      estimated_cost_usd: 0.25,
      cost_partial: true,
      input_tokens: 310000,
      output_tokens: 120000,
      cache_read_tokens: 9400000,
      cache_create_tokens: 640000,
    });
  });

  it('keeps an absent estimate absent', () => {
    expect(fromFleetSpendView(fleetSpendView({ estimated_cost_usd: null })).estimated_cost_usd).toBeNull();
  });
});
