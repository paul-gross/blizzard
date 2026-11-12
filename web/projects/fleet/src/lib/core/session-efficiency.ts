import type { ChunkUsageTotalView } from '../api/hub';
import { formatCost, formatTokens } from './cost-format';

/** The usage fields the session efficiency cost reads — the wire's own names, so a chunk's, a step's or the fleet's
 * usage total passes straight through. */
export type SessionEfficiencyInput = Pick<
  ChunkUsageTotalView,
  | 'cost_usd'
  | 'estimated_cost_usd'
  | 'cost_partial'
  | 'input_tokens'
  | 'output_tokens'
  | 'cache_read_tokens'
  | 'cache_create_tokens'
>;

/** The session efficiency cost: `$0.30 · 220kI / 32.7kO × 2.57%` — the cost, the distinct input (input plus cache
 * write, cache read excluded), the output, and the cache miss percent (distinct input over distinct input plus cache
 * read; `—` with no input at all). Every figure but the cost reads to three significant digits. */
export function formatSessionEfficiency(u: SessionEfficiencyInput): string {
  return sessionEfficiencyParts(u).join(' ');
}

/** {@link formatSessionEfficiency}'s figure as its four parts, each separator leading the part it introduces —
 * `$0.30`, `· 220kI`, `/ 32.7kO`, `× 2.57%` — so a wrapping view breaks between parts and never strands one. With no tokens at all, only the cost. */
export function sessionEfficiencyParts(u: SessionEfficiencyInput): readonly string[] {
  const distinct = u.input_tokens + u.cache_create_tokens;
  const cost = formatCost(u.cost_usd, u.estimated_cost_usd, u.cost_partial);
  if (distinct + u.output_tokens + u.cache_read_tokens === 0) return [cost];
  const miss = cacheMissPercent(distinct, u.cache_read_tokens);
  return [
    cost,
    `· ${formatTokens(distinct)}I`,
    `/ ${formatTokens(u.output_tokens)}O`,
    `× ${miss === null ? '—' : `${sigPercent(miss)}%`}`,
  ];
}

/** The {@link formatSessionEfficiency} figure spelled out in full, for its hover title. */
export function sessionEfficiencyTitle(u: SessionEfficiencyInput): string {
  const distinct = u.input_tokens + u.cache_create_tokens;
  const miss = cacheMissPercent(distinct, u.cache_read_tokens);
  const n = (count: number): string => count.toLocaleString('en-US');
  return (
    `session efficiency cost: ${formatCost(u.cost_usd, u.estimated_cost_usd, u.cost_partial)} · ${n(distinct)} input (in + cache write) · ` +
    `${n(u.output_tokens)} output · ${n(u.cache_read_tokens)} cache read · ${miss === null ? '—' : `${sigPercent(miss)}%`} cache miss`
  );
}

function cacheMissPercent(distinct: number, cacheRead: number): number | null {
  const total = distinct + cacheRead;
  return total === 0 ? null : (distinct / total) * 100;
}

/** A percent to three significant digits — `0.412`, `2.57`, `12.3` — and `100` at or above a hundred. */
function sigPercent(percent: number): string {
  if (percent === 0) return '0';
  const p = Number(percent.toPrecision(3));
  return p >= 100 ? '100' : percent.toPrecision(3);
}
