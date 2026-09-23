/**
 * A derived billed-cost total's board/CLI legible form — always to the cent, with a
 * leading `~` when the total is `cost_partial`, a **lower bound** rather than the true
 * spend (when a total is partial: `src/blizzard/hub/domain/work.py`'s `UsageTotal`). Every
 * surface that renders a `ChunkUsageTotalView`/`FleetSpendView` billed cost — the board
 * card, the chunk detail panel, and `blizzard hub status` — reads it through this one
 * function so the partial marker never silently drops. A row's own estimate never enters
 * this figure; see {@link formatCostEstimate}.
 */
export function formatCost(costUsd: number, costPartial: boolean): string {
  const amount = `$${costUsd.toFixed(2)}`;
  return costPartial ? `~${amount}` : amount;
}

/**
 * A cost estimate's board legible form — the one formatter for `estimated_cost_usd`,
 * mirroring `hub/cli/views.py`'s `CostEstimate.rendered`. Always labeled `est.`, never
 * prefixed `~`: an estimate is already labeled as such, not a lower bound of anything
 * (`~` is {@link formatCost}'s own, distinct, partial marker). Every surface that renders
 * an estimate reads it through this one function, alongside `formatCost`, never merged
 * into it.
 */
export function formatCostEstimate(amountUsd: number): string {
  return `$${amountUsd.toFixed(2)} est.`;
}

/** A token count's board/CLI legible form — `1.2k`/`3.4M` above 1000, exact below,
 * so a large chunk's tokens-by-class breakdown stays scannable in a narrow column. */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}
