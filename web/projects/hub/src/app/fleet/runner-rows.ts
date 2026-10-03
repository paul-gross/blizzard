import { computed } from '@angular/core';

import { type ChunkStatus, type ExternalSubscriptionUsageWindowView, type RunnerView, compactRef, type KitAsyncStateValue, injectNowSignal, ageMs, formatRefreshedAgo, asyncState } from 'fleet';
import { injectHubBoardChunksQuery } from '../board/chunks/chunks.query';
import { injectHubRunnersQuery } from './runners.query';

/** One claim line under a registry row: the chunk a runner holds, where it sits, and
 * how it is doing there — the node alone reads the same for a chunk actively
 * running and one parked `needs_human`. */
export interface ClaimLine {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
  readonly status: ChunkStatus;
}

/** One rate-limit window's pacing pair for the registry row's pace bar —
 * `window` is the harness-native label (`"5h"`/`"7d"`), `utilizationPct` is read
 * straight off the sample, `elapsedPct` is derived (see {@link windowElapsedPct}). */
export interface PaceBar {
  readonly window: string;
  readonly utilizationPct: number;
  readonly elapsedPct: number;
}

/** The board's own age tiers for a declared subscription's last good sample —
 * `bzh:frontend-formatters`: one classifier, read by the fold below and by nothing
 * else (not the template, the CSS, or the mobile view). */
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

/** One reported subscription sample's pace bars, grouped under its slug and name. Two
 * subscriptions can share a window label (both report a `"5h"` window), so grouping
 * by slug is what keeps them distinct. */
export interface SubscriptionPace {
  readonly slug: string;
  readonly name: string;
  readonly paceBars: readonly PaceBar[];
  /** `'credential_lapsed'` when the newest reported miss outranks the newest sample;
   * `null` otherwise. */
  readonly condition: string | null;
  /** The last good sample's raw instant, or `null` when the slug has never been
   * sampled — presence, not `paceBars.length`, is what tells a zero-window sample
   * apart from no sample at all. */
  readonly sampledAt: string | null;
  /** "refreshed 5m ago" against {@link sampledAt}, or `null` alongside it. */
  readonly refreshedLabel: string | null;
  /** {@link sampledAt}'s age tier, or `null` alongside it — `null` too for a stamp
   * unparseable or beyond {@link SKEW_TOLERANCE_MS} in the future
   * (`bzh:utc-instants`): never a confident tier for a reading the clock can't trust. */
  readonly freshness: SubscriptionFreshness | null;
  /** The newest reported miss's own reason, or `null` when there is none. */
  readonly missReason: string | null;
}

/** A registry row: the runner plus its claims and subscription pace groups, pre-folded
 * so a presentational view needs no second read to render them. `used` is the slot
 * bar's numerator — environments held by this runner's live routes. */
export interface RunnerRow extends RunnerView {
  readonly claims: readonly ClaimLine[];
  readonly used: number;
  readonly subscriptionPaces: readonly SubscriptionPace[];
  /** The registry's one clock reading (`injectNowSignal`), so every time-relative label on
   * the row — pace bars, "seen Ns ago" — ticks together rather than each reading `Date.now()`. */
  readonly nowMs: number;
}

/** Why the runner stopped itself: a spend-ceiling crossing names the ceiling and the
 * spend it reported (`locally_paused_reason`); a manual pause carries none, so this
 * falls back to a generic clear-it-yourself hint. */
export function localPauseHint(row: RunnerRow): string {
  return row.locally_paused_reason ?? 'This runner paused itself. Clear it on the runner: blizzard runner start';
}

/** Why resuming at the hub may not start a runner: its own brake is not the hub's to
 * clear. */
export function runnerToggleHint(row: RunnerRow): string {
  if (row.hub_paused && row.locally_paused) {
    return 'Resuming here clears the hub brake only — this runner also paused itself.';
  }
  return row.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
}

/**
 * How far a rate-limit window has elapsed toward its own reset, `0-100`.
 * Derived **backward** from `resetsAt`/`windowSeconds` rather than assumed clock-aligned
 * — the 5h window is session-anchored, not aligned to any fixed clock boundary, so a
 * window's start is never assumed, only computed as `resetsAt - windowSeconds`. Clamped
 * to `[0, 100]`: a `resetsAt` more than a full window out (not yet started) reads 0, one
 * already passed (a stale sample) reads 100 rather than overshooting.
 *
 * `nowMs` is the caller's own clock reading (`injectNowSignal()`'s value in the
 * container) — this function does no clock reads of its own, so it stays directly
 * testable against a fixed instant.
 */
export function windowElapsedPct(nowMs: number, resetsAt: string, windowSeconds: number): number {
  const resetsAtMs = Date.parse(resetsAt);
  if (Number.isNaN(resetsAtMs) || windowSeconds <= 0) return 0;
  const windowMs = windowSeconds * 1000;
  const startMs = resetsAtMs - windowMs;
  const fraction = (nowMs - startMs) / windowMs;
  return Math.min(100, Math.max(0, fraction * 100));
}

/** A subscription's windows folded to pace bars against `now`. */
function toPaceBars(now: number, windows: readonly ExternalSubscriptionUsageWindowView[]): readonly PaceBar[] {
  return windows.map((w) => ({
    window: w.window,
    utilizationPct: w.utilization_pct,
    elapsedPct: windowElapsedPct(now, w.resets_at, w.window_seconds),
  }));
}

/**
 * Folds the runners + chunks reads into {@link RunnerRow}s: each runner's claims,
 * slot-bar numerator, and pace bars, derived once per registry query
 * (`bzh:frontend-formatters`).
 *
 * Retired runners are folded in only while `includeRetired()` is true. Returns only
 * the shared reads and their fold; permission reads and pause mutations are left to
 * the caller.
 */
export function injectRunnerRows(includeRetired: () => boolean = () => false): {
  readonly rows: () => readonly RunnerRow[];
  readonly state: () => KitAsyncStateValue;
} {
  const runnersQuery = injectHubRunnersQuery(includeRetired);
  const chunksQuery = injectHubBoardChunksQuery();

  const runners = computed<readonly RunnerView[]>(() => runnersQuery.data() ?? []);

  const state = computed<KitAsyncStateValue>(() => asyncState(runnersQuery, runners().length === 0));

  /** Every routed chunk grouped by the runner holding it — each as a claim line
   * (short name + current node + status) for the registry rows.
   *
   * No status filter by design: `ChunkSummary.runner_id` is already in-progress-only
   * (see its own docs), so `runner_id` set *is* "currently holds a route on". */
  const claims = computed<Map<string, ClaimLine[]>>(() => {
    const grouped = new Map<string, ClaimLine[]>();
    for (const chunk of chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      const lines = grouped.get(chunk.runner_id) ?? [];
      lines.push({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        status: chunk.status,
      });
      grouped.set(chunk.runner_id, lines);
    }
    return grouped;
  });

  /** Environments held per runner — the slot bar's numerator, summed from
   * each of its chunks' `environment_count`. A grouped chunk holding >1 environment
   * counts them all, so a runner working one 3-env chunk reads as using 3 slots, not 1.
   * Environments are exclusively leased and a runner's chunks are distinct, so a plain
   * sum needs no dedup. Unfiltered by status for the same reason {@link claims} is. */
  const usedByRunner = computed<Map<string, number>>(() => {
    const used = new Map<string, number>();
    for (const chunk of chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      used.set(chunk.runner_id, (used.get(chunk.runner_id) ?? 0) + (chunk.environment_count ?? 0));
    }
    return used;
  });

  /** A slow-ticking clock — this data refreshes on `runner-changed` SSE,
   * not by polling, so the only job here is keeping each pace bar's *elapsed* fraction
   * visually live between those pushes, not driving fresh reads. */
  const now = injectNowSignal(30_000);

  /** Each runner's reported subscription samples, grouped by slug. A runner that has
   * reported none maps to an empty list. Recomputes on {@link now} so elapsed bars
   * keep advancing even between `runner-changed` pushes. */
  const subscriptionPacesByRunner = computed<Map<string, readonly SubscriptionPace[]>>(() => {
    const nowMs = now();
    const grouped = new Map<string, readonly SubscriptionPace[]>();
    for (const runner of runners()) {
      const subscriptions = runner.subscriptions ?? [];
      grouped.set(
        runner.runner_id,
        subscriptions.map((s) => {
          const sampledAt = s.sampled_at ?? null;
          const delta = sampledAt === null ? null : ageMs(sampledAt, nowMs);
          return {
            slug: s.slug,
            name: s.name,
            paceBars: toPaceBars(nowMs, s.windows),
            condition: s.condition ?? null,
            sampledAt,
            refreshedLabel: delta === null ? null : formatRefreshedAgo(delta),
            freshness: delta === null ? null : classifySubscriptionFreshness(delta),
            missReason: s.miss_reason ?? null,
          };
        }),
      );
    }
    return grouped;
  });

  /** Each runner with its claims, slot-bar numerator, and subscription pace groups folded on. */
  const rows = computed<readonly RunnerRow[]>(() =>
    runners().map((runner) => ({
      ...runner,
      claims: claims().get(runner.runner_id) ?? [],
      used: usedByRunner().get(runner.runner_id) ?? 0,
      subscriptionPaces: subscriptionPacesByRunner().get(runner.runner_id) ?? [],
      nowMs: now(),
    })),
  );

  return { rows, state };
}
