import type { FleetSpendView } from 'fleet/shell';

import type { FleetSpend } from './fleet-spend.domain';

export function fromFleetSpendView(wire: FleetSpendView): FleetSpend {
  return {
    cost_usd: wire.cost_usd, estimated_cost_usd: wire.estimated_cost_usd, cost_partial: wire.cost_partial,
    input_tokens: wire.input_tokens, output_tokens: wire.output_tokens,
    cache_read_tokens: wire.cache_read_tokens, cache_create_tokens: wire.cache_create_tokens,
  };
}
