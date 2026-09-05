import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { ChunkStatus, ExternalSubscriptionUsageWindowView, RunnerView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { compactRef } from '../compact-ref';
import { injectHubChunksQuery } from '../chunks/chunks.query';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import { injectNowSignal } from '../now-signal';
import { asyncState } from '../query-state';
import { RunnerPanelView } from './runner-view';
import { injectHubRunnersQuery } from './runners.query';
import { injectRunnerPauseMutation } from './runners.mutations';

/** One claim line under a registry row: the chunk a runner holds, where it sits, and
 * how it is doing there (issue #156) — the node alone reads the same for a chunk
 * actively running and one parked `needs_human`. */
export interface ClaimLine {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
  readonly status: ChunkStatus;
}

/** One rate-limit window's pacing pair for the registry row's pace bar (issue #218) —
 * `window` is the harness-native label (`"5h"`/`"7d"`), `utilizationPct` is read
 * straight off the sample, `elapsedPct` is derived (see {@link windowElapsedPct}). */
export interface PaceBar {
  readonly window: string;
  readonly utilizationPct: number;
  readonly elapsedPct: number;
}

/** One declared subscription's own pace bars, grouped under its slug and name
 * (blizzard#436) — additive beside the legacy single-subscription {@link PaceBar}
 * list, and not yet rendered (blizzard#478 owns the render; this issue owns only the
 * data model). Two subscriptions can share a window label (both report a `"5h"`
 * window), so grouping by slug is what keeps them distinct. */
export interface SubscriptionPace {
  readonly slug: string;
  readonly name: string;
  readonly paceBars: readonly PaceBar[];
}

/** A registry row: the runner plus the claims it holds, pre-folded so the
 * presentational sibling needs no second read to render them. `used` is the slot
 * bar's numerator — environments held by this runner's live routes (issue #69).
 * `paceBars` is empty when the runner has never sampled its external-subscription
 * usage, or the sample is stale — the hub already nulls `external_subscription_usage`
 * in that case (issue #218), so this row never needs to re-derive staleness itself.
 * `subscriptionPaces` is the per-slug grouping of the same windows (blizzard#436), derived
 * from the wire's additive `subscriptions` collection — empty for a runner that has
 * declared none, including one still on the legacy single-subscription shape alone. */
export interface RunnerRow extends RunnerView {
  readonly claims: readonly ClaimLine[];
  readonly used: number;
  readonly paceBars: readonly PaceBar[];
  readonly subscriptionPaces: readonly SubscriptionPace[];
}

/**
 * How far a rate-limit window has elapsed toward its own reset, `0-100` (issue #218).
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

/** A subscription's windows folded to pace bars against `now` — the shared step
 * between the legacy single-subscription bars and the per-slug grouped ones. */
function toPaceBars(now: number, windows: readonly ExternalSubscriptionUsageWindowView[]): readonly PaceBar[] {
  return windows.map((w) => ({
    window: w.window,
    utilizationPct: w.utilization_pct,
    elapsedPct: windowElapsedPct(now, w.resets_at, w.window_seconds),
  }));
}

/**
 * The runner panel — the fleet registry in the board's right rail: each
 * registered runner with its derived **liveness** (`online` vs the
 * staleness threshold), last-seen time, and **paused** state, plus a pause/resume
 * toggle — the operator's brake, declarative state the runner reads on its
 * outbound pull.
 *
 * A container (issue #80): it owns the registry + chunks queries and the pause
 * mutation, through the generated client (bzh:generated-client), folds each
 * runner's claims off the chunk list, and renders the presentational
 * {@link RunnerPanelView}. The live-update service re-reads on `runner-changed`.
 */
@Component({
  selector: 'fleet-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerPanelView],
  templateUrl: './runner-panel.html',
})
export class RunnerPanel {
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier — issue #93). Passed to the presentational view, which
   * withholds the toggle button when false so a `contributor` never sees a control that
   * would 403. `null`/pending resolves to `false` (hidden until confirmed). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** The fleet registry; empty until the first read resolves. */
  private readonly runners = computed<readonly RunnerView[]>(() => this.runnersQuery.data() ?? []);

  /** The registry's async state (AC 3) — derived from the runners query alone;
   * the chunks query only folds claims onto rows already known to exist. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.runnersQuery, this.runners().length === 0),
  );

  /** Every routed chunk grouped by the runner holding it — each as a claim line
   * (short name + current node + status) for the registry rows.
   *
   * No status filter by design: `ChunkSummary.runner_id` is already in-progress-only
   * (issue #140 — see its own docs), so `runner_id` set *is* "currently holds a route on". */
  private readonly claims = computed<Map<string, ClaimLine[]>>(() => {
    const grouped = new Map<string, ClaimLine[]>();
    for (const chunk of this.chunksQuery.data() ?? []) {
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

  /** Environments held per runner — the slot bar's numerator (issue #69), summed from
   * each of its chunks' `environment_count`. A grouped chunk holding >1 environment
   * counts them all, so a runner working one 3-env chunk reads as using 3 slots, not 1.
   * Environments are exclusively leased and a runner's chunks are distinct, so a plain
   * sum needs no dedup. Unfiltered by status for the same reason {@link claims} is. */
  private readonly usedByRunner = computed<Map<string, number>>(() => {
    const used = new Map<string, number>();
    for (const chunk of this.chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      used.set(chunk.runner_id, (used.get(chunk.runner_id) ?? 0) + (chunk.environment_count ?? 0));
    }
    return used;
  });

  /** A slow-ticking clock (issue #218) — this data refreshes on `runner-changed` SSE,
   * not by polling, so the only job here is keeping each pace bar's *elapsed* fraction
   * visually live between those pushes, not driving fresh reads. */
  private readonly now = injectNowSignal(30_000);

  /** Each runner's pace bars, one per external-subscription window — empty for a
   * runner that has never sampled, or whose sample the hub has already nulled as
   * stale (issue #218). Recomputes on {@link now}'s tick as well as on fresh data, so
   * the elapsed bar keeps advancing even between `runner-changed` pushes. */
  private readonly paceBarsByRunner = computed<Map<string, readonly PaceBar[]>>(() => {
    const now = this.now();
    const bars = new Map<string, readonly PaceBar[]>();
    for (const runner of this.runners()) {
      const usage = runner.external_subscription_usage;
      if (!usage) continue;
      bars.set(runner.runner_id, toPaceBars(now, usage.windows));
    }
    return bars;
  });

  /** Each runner's declared subscriptions, grouped by slug (blizzard#436) — the
   * per-slug counterpart to {@link paceBarsByRunner}'s legacy single-subscription
   * bars. A runner that has declared no subscriptions, including one still reporting
   * only through the legacy field, maps to an empty list. Recomputes on {@link now}
   * for the same reason {@link paceBarsByRunner} does. */
  private readonly subscriptionPacesByRunner = computed<Map<string, readonly SubscriptionPace[]>>(() => {
    const now = this.now();
    const grouped = new Map<string, readonly SubscriptionPace[]>();
    for (const runner of this.runners()) {
      const subscriptions = runner.subscriptions ?? [];
      grouped.set(
        runner.runner_id,
        subscriptions.map((s) => ({
          slug: s.slug,
          name: s.name,
          paceBars: toPaceBars(now, s.windows),
        })),
      );
    }
    return grouped;
  });

  /** Each runner with its claims, slot-bar numerator, and pace bars folded on, for the
   * view. */
  protected readonly rows = computed<readonly RunnerRow[]>(() =>
    this.runners().map((runner) => ({
      ...runner,
      claims: this.claims().get(runner.runner_id) ?? [],
      used: this.usedByRunner().get(runner.runner_id) ?? 0,
      paceBars: this.paceBarsByRunner().get(runner.runner_id) ?? [],
      subscriptionPaces: this.subscriptionPacesByRunner().get(runner.runner_id) ?? [],
    })),
  );

  protected toggle(runner: RunnerView): void {
    this.pauseMutation.mutate({ runnerId: runner.runner_id, paused: !runner.hub_paused });
  }
}
