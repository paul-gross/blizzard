import { formatCost } from './cost-format';

/** One step's token classes and cost — what the session efficiency cost reads. */
export interface SessionEfficiencyInput {
  readonly costUsd: number;
  readonly estimatedCostUsd: number | null;
  readonly costPartial: boolean;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cacheReadTokens: number;
  readonly cacheCreateTokens: number;
}

/** The session efficiency cost: `$0.30 · 220kI / 32.7kO × 2.57%` — the cost, the distinct input (input plus cache
 * write, cache read excluded), the output, and the cache miss percent (distinct input over distinct input plus cache
 * read; `—` with no input at all). Every figure but the cost reads to three significant digits. */
export function formatSessionEfficiency(u: SessionEfficiencyInput): string {
  return sessionEfficiencyParts(u).join(' ');
}

/** {@link formatSessionEfficiency}'s figure as its four parts, each separator leading the part it introduces —
 * `$0.30`, `· 220kI`, `/ 32.7kO`, `× 2.57%` — so a wrapping view breaks between parts and never strands one. With no tokens at all, only the cost. */
export function sessionEfficiencyParts(u: SessionEfficiencyInput): readonly string[] {
  const distinct = u.inputTokens + u.cacheCreateTokens;
  const cost = formatCost(u.costUsd, u.estimatedCostUsd, u.costPartial);
  if (distinct + u.outputTokens + u.cacheReadTokens === 0) return [cost];
  const miss = cacheMissPercent(distinct, u.cacheReadTokens);
  return [
    cost,
    `· ${sigTokens(distinct)}I`,
    `/ ${sigTokens(u.outputTokens)}O`,
    `× ${miss === null ? '—' : `${sigPercent(miss)}%`}`,
  ];
}

/** The {@link formatSessionEfficiency} figure spelled out in full, for its hover title. */
export function sessionEfficiencyTitle(u: SessionEfficiencyInput): string {
  const distinct = u.inputTokens + u.cacheCreateTokens;
  const miss = cacheMissPercent(distinct, u.cacheReadTokens);
  const n = (count: number): string => count.toLocaleString('en-US');
  return (
    `session efficiency cost: ${formatCost(u.costUsd, u.estimatedCostUsd, u.costPartial)} · ${n(distinct)} input (in + cache write) · ` +
    `${n(u.outputTokens)} output · ${n(u.cacheReadTokens)} cache read · ${miss === null ? '—' : `${sigPercent(miss)}%`} cache miss`
  );
}

function cacheMissPercent(distinct: number, cacheRead: number): number | null {
  const total = distinct + cacheRead;
  return total === 0 ? null : (distinct / total) * 100;
}

/** A token count to three significant digits — `850`, `32.7k`, `1.23M`. */
function sigTokens(count: number): string {
  if (count < 1000) return String(Math.round(count));
  const k = Number((count / 1000).toPrecision(3));
  if (k < 1000) return `${(count / 1000).toPrecision(3)}k`;
  return `${(count / 1_000_000).toPrecision(3)}M`;
}

/** A percent to three significant digits — `0.412`, `2.57`, `12.3` — and `100` at or above a hundred. */
function sigPercent(percent: number): string {
  if (percent === 0) return '0';
  const p = Number(percent.toPrecision(3));
  return p >= 100 ? '100' : percent.toPrecision(3);
}
