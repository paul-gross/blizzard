/**
 * A derived spend total as one figure, to the cent: `costUsd + (estimatedCostUsd ?? 0)`,
 * with a leading `~` when `estimatedCostUsd` is present (even `0`) and a trailing `+` when
 * `costPartial` is true. The markers' meaning is owned by docs/deployment/spend.md and the
 * wire's usage totals (`estimated_cost_usd`, `cost_partial`).
 */
export function formatCost(costUsd: number, estimatedCostUsd: number | null | undefined, costPartial: boolean): string {
  const amount = costUsd + (estimatedCostUsd ?? 0);
  const prefix = estimatedCostUsd != null ? '~' : '';
  const suffix = costPartial ? '+' : '';
  return `${prefix}$${amount.toFixed(2)}${suffix}`;
}

/** Whether a total has anything to show — a billed amount, an estimate, or a partial
 * mark — so a card or row withholds the figure only on an entirely empty total. */
export function hasCostFigure(costUsd: number, estimatedCostUsd: number | null | undefined, costPartial: boolean): boolean {
  return costUsd > 0 || estimatedCostUsd != null || costPartial;
}

/** A token count's board/CLI legible form — `1.2k`/`3.4M` above 1000, exact below,
 * so a large chunk's tokens-by-class breakdown stays scannable in a narrow column. */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}
