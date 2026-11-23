import { describe, expect, it } from 'vitest';
import { fleetSpendView } from 'fleet/testing';

import { fromFleetSpendView } from './fleet-spend.translate';

describe('fromFleetSpendView', () => {
  it('carries the exact cost, the estimate and whether the cost is partial', () => {
    expect(fromFleetSpendView(fleetSpendView({ cost_usd: 3, estimated_cost_usd: 0.25, cost_partial: true }))).toEqual({
      cost_usd: 3,
      estimated_cost_usd: 0.25,
      cost_partial: true,
    });
  });

  it('keeps an absent estimate absent', () => {
    expect(fromFleetSpendView(fleetSpendView({ estimated_cost_usd: null })).estimated_cost_usd).toBeNull();
  });
});
