/**
 * A derived spend total's one board/CLI legible figure — always to the cent, folding
 * `costUsd` and `estimatedCostUsd` into the single amount an operator reads as "what
 * this cost": `costUsd + (estimatedCostUsd ?? 0)`. Two independent markers ride the
 * one figure rather than a second, separate one: a leading `~` whenever
 * `estimatedCostUsd` is present (`!= null`, even when it is `0` — some part of the
 * amount is estimated, not billed), and a trailing `+` whenever `costPartial` is
 * `true` (some summed row carried no amount at all, so the figure is a **lower
 * bound** rather than the true spend — `src/blizzard/hub/domain/work.py`'s
 * `UsageTotal`). The two combine freely: `$4.00`, `~$4.05`, `$4.00+`, `~$4.05+`, and
 * an entirely-estimated total (nothing billed yet) reads `~$0.07` on its own. Every
 * surface that renders a `ChunkUsageTotalView`/`FleetSpendView`/`StepUsageTotal`
 * cost — the board card, the chunk detail panel, the glance board, and
 * `blizzard hub status` — reads it through this one function so neither marker ever
 * silently drops.
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
