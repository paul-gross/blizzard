import type { HeaderSpend } from './board-header';

export function headerSpend(overrides: Partial<HeaderSpend> = {}): HeaderSpend {
  return { cost_usd: 27.5, estimated_cost_usd: null, cost_partial: false, ...overrides };
}
