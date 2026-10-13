import type { ExternalSubscriptionUsageWindowView, SampleMissReason } from '../api/hub';

/** One rate-limit window's pacing pair for a subscription's pace bar —
 * `window` is the harness-native label (`"5h"`/`"7d"`), `utilizationPct` is read
 * straight off the sample, `elapsedPct` is derived (see {@link windowElapsedPct}). */
export interface PaceBar {
  readonly window: string;
  readonly utilizationPct: number;
  readonly elapsedPct: number;
}

/** The board's own age tiers for a subscription's last good sample —
 * `bzh:frontend-formatters`: one classifier, read by the folds and by nothing
 * else (not the template or the CSS). */
export type SubscriptionFreshness = 'fresh' | 'aging' | 'stale';

/** `sampledAt` at or under this age is `'fresh'`. */
export const FRESHNESS_AGING_AFTER_MS = 15 * 60_000;
/** `sampledAt` at or under this age (and past {@link FRESHNESS_AGING_AFTER_MS}) is
 * `'aging'`; past it, `'stale'`. */
export const FRESHNESS_STALE_AFTER_MS = 60 * 60_000;

/** Maps an exact age in ms to its display tier — boundaries are measured on the exact
 * `ageMs`, never on the rounded "refreshed … ago" label, so a sample at precisely
 * 15m0s reads `'fresh'` even though its label already rounds to `15m`. */
export function classifySubscriptionFreshness(sampleAgeMs: number): SubscriptionFreshness {
  if (sampleAgeMs <= FRESHNESS_AGING_AFTER_MS) return 'fresh';
  if (sampleAgeMs <= FRESHNESS_STALE_AFTER_MS) return 'aging';
  return 'stale';
}

/** A daemon's own newest sampling attempt in operator words — the runner's dashboard
 * reports whether its last attempt succeeded rather than usage windows, so its rows
 * carry this line in place of pace bars. `ok` drives the tone (`null` = never sampled,
 * `false` = miss), carried alongside the already-composed {@link conditionLabel}. */
export interface SubscriptionSampleLine {
  readonly conditionLabel: string;
  readonly sampledAgo: string;
  readonly ok: boolean | null;
}

/** One subscription as the shared subscription list renders it, keyed by slug. Two
 * subscriptions can share a window label (both report a `"5h"` window), so grouping
 * by slug is what keeps them distinct. */
export interface SubscriptionPace {
  readonly slug: string;
  readonly name: string;
  readonly paceBars: readonly PaceBar[];
  /** `SampleMissReason.CREDENTIAL_LAPSED` when the newest reported miss outranks the newest sample;
   * `null` otherwise. */
  readonly condition: SampleMissReason | null;
  /** The last good sample's raw instant, or `null` when the slug has never been
   * sampled — presence, not `paceBars.length`, is what tells a zero-window sample
   * apart from no sample at all. */
  readonly sampledAt: string | null;
  /** "refreshed 5m ago" against {@link sampledAt}, or `null` alongside it. */
  readonly refreshedLabel: string | null;
  /** {@link sampledAt}'s age tier, or `null` alongside it — `null` too for a stamp
   * unparseable or beyond the clock-skew tolerance in the future
   * (`bzh:utc-instants`): never a confident tier for a reading the clock can't trust. */
  readonly freshness: SubscriptionFreshness | null;
  /** The newest reported miss's own reason, or `null` when there is none. */
  readonly missReason: SampleMissReason | null;
  /** The subscription's provider, shown beside its name when set. */
  readonly provider?: string | null;
  /** The daemon's own sampling line; when set it renders in place of the pace bars. */
  readonly sample?: SubscriptionSampleLine | null;
  /** The display names of the runners spending this subscription; empty or absent renders no spender line. */
  readonly spenders?: readonly string[];
  /** The newest credential renewal in operator words, or `null`/absent when never renewed. */
  readonly renewalLabel?: string | null;
}

/**
 * How far a rate-limit window has elapsed toward its own reset, `0-100`.
 * Derived **backward** from `resetsAt`/`windowSeconds` rather than assumed clock-aligned
 * — the 5h window is session-anchored, not aligned to any fixed clock boundary, so a
 * window's start is never assumed, only computed as `resetsAt - windowSeconds`. Clamped
 * to `[0, 100]`: a `resetsAt` more than a full window out (not yet started) reads 0, one
 * already passed (a stale sample) reads 100 rather than overshooting.
 *
 * `nowMs` is the caller's own clock reading — this function does no clock reads of its
 * own, so it stays directly testable against a fixed instant.
 */
export function windowElapsedPct(nowMs: number, resetsAt: string, windowSeconds: number): number {
  const resetsAtMs = Date.parse(resetsAt);
  if (Number.isNaN(resetsAtMs) || windowSeconds <= 0) return 0;
  const windowMs = windowSeconds * 1000;
  const startMs = resetsAtMs - windowMs;
  const fraction = (nowMs - startMs) / windowMs;
  return Math.min(100, Math.max(0, fraction * 100));
}

/** A subscription's windows folded to pace bars against `nowMs`. */
export function toPaceBars(nowMs: number, windows: readonly ExternalSubscriptionUsageWindowView[]): readonly PaceBar[] {
  return windows.map((w) => ({
    window: w.window,
    utilizationPct: w.utilization_pct,
    elapsedPct: windowElapsedPct(nowMs, w.resets_at, w.window_seconds),
  }));
}
