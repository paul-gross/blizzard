import type { FleetSpend } from './fleet-spend.domain';

export function fleetSpend(overrides: Partial<FleetSpend> = {}): FleetSpend {
  return { cost_usd: 0, estimated_cost_usd: null, cost_partial: false, ...overrides };
}
