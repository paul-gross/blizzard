/**
 * A derived cost total's board/CLI legible form (issue #60) — always to the cent, with
 * a leading `~` when the total is `cost_partial` (a crash/reap-path row had no envelope,
 * so the summed dollar figure is a **lower bound**, not the true spend). Every surface
 * that renders a `ChunkUsageTotalView`/`FleetSpendView` cost — the board card, the chunk
 * detail panel, and `blizzard hub status` — reads it through this one function so the
 * partial marker never silently drops.
 */
export function formatCost(costUsd: number, costPartial: boolean): string {
  const amount = `$${costUsd.toFixed(2)}`;
  return costPartial ? `~${amount}` : amount;
}

/** A token count's board/CLI legible form — `1.2k`/`3.4M` above 1000, exact below,
 * so a large chunk's tokens-by-class breakdown stays scannable in a narrow column. */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}
