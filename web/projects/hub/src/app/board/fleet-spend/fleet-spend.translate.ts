import type { FleetSpendView } from 'fleet/shell';

import type { FleetSpend } from './fleet-spend.domain';

export function fromFleetSpendView(wire: FleetSpendView): FleetSpend {
  return { cost_usd: wire.cost_usd, estimated_cost_usd: wire.estimated_cost_usd, cost_partial: wire.cost_partial };
}
