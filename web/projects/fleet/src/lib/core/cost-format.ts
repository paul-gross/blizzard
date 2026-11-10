/** The tooltip a step's cost figure carries when its `+` suffix marks a lower bound. */
export const STEP_COST_PARTIAL_TITLE =
  "At least one invocation's cost was absent (a crash/reap-path exit) — this step's cost is a lower bound.";

/** The tooltip a chunk's total cost figure carries when its `+` suffix marks a lower bound. */
export const TOTAL_COST_PARTIAL_TITLE =
  "At least one invocation's cost was absent (a crash/reap-path exit) — this total is a lower bound, not the true spend.";

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

/** A token count to three significant digits — `850`, `32.7k`, `1.23M`. */
export function formatTokens(count: number): string {
  if (count < 1000) return String(Math.round(count));
  const k = Number((count / 1000).toPrecision(3));
  if (k < 1000) return `${(count / 1000).toPrecision(3)}k`;
  return `${(count / 1_000_000).toPrecision(3)}M`;
}
