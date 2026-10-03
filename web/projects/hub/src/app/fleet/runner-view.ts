import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type ChunkStatus, STATUS_TONE, KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitPanel, KitSlotBar, type Tone, formatSeenAgo } from 'fleet';
import { CapabilityBadgeGroup } from './capability-badge-group';
import { GateBadgeGroup } from './gate-badge-group';
import { localPauseHint, runnerToggleHint, type RunnerRow } from './runner-rows';
import { SubscriptionPaceGroup } from './subscription-pace-group';

/**
 * The runner registry's presentational half — the registry
 * table's markup, liveness dot, claim lines, pause-brake badges, and the
 * pause/resume toggle. Renders exactly the rows it is handed; injects no
 * query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'app-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CapabilityBadgeGroup, GateBadgeGroup, KitAsyncState, KitBadge, KitButton, KitPanel, KitSlotBar, SubscriptionPaceGroup],
  templateUrl: './runner-view.html',
  styleUrl: './runner-view.css',
})
export class RunnerPanelView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The registry query's async state (AC 3) — loading/error withhold the empty
   * copy until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether to render the hub pause/resume brake — the identity's
   * `runner:pause` permission. Admin-tier: a `contributor` sees the registry
   * and its liveness/paused badges but not the toggle it could only 403 on. Defaults
   * `false` so the brake stays withheld until permission is confirmed (no flash of a
   * control the identity cannot use). */
  readonly canPause = input(false);

  /** The runner ids with a pause/resume currently in flight — only those rows'
   * toggles disable, so every other row's stays enabled. */
  readonly pendingRunnerIds = input<readonly string[]>([]);

  /** The panel's last pause/resume failure, or `null` — rendered as a visible inline
   * notice near the registry ("report, don't swallow"). */
  readonly actionError = input<string | null>(null);

  /** Whether `row`'s own hub pause/resume mutation is in flight — the per-row
   * membership check against {@link pendingRunnerIds}. */
  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }

  /** A claim's badge tone, read straight off `chunk-lanes.ts`'s `STATUS_TONE` — the
   * single owner of the status→tone fold the board card colors from too.
   * No local table: a claim's color and its card's can never drift apart. */
  protected toneFor(status: ChunkStatus): Tone {
    return STATUS_TONE[status];
  }

  /** Whether to render the env-slot bar: only when the runner reported a
   * capacity. A runner registered by a client that predates the field has a null (or
   * absent) `env_capacity` and gets no bar, rather than a misleading zero-slot one. */
  protected hasCapacity(row: RunnerRow): boolean {
    return row.env_capacity !== null && row.env_capacity !== undefined;
  }

  /** Emitted with the row to flip the **hub** brake on — the container reads
   * `hub_paused` off it to decide pause vs. resume. Named `togglePause`, not
   * `toggle` — `@angular-eslint/no-output-native` forbids an output shadowing
   * the native DOM `toggle` event. */
  readonly togglePause = output<RunnerRow>();

  protected readonly localPauseHint = localPauseHint;

  protected readonly toggleHint = runnerToggleHint;

  /**
   * A compact "seen 12s ago" liveness label from `last_seen_at` (`bzh:utc-instants`).
   *
   * Liveness is decided where both instants share one clock — the hub, via `online`
   * (`derive_online` compares `last_seen_at` against the hub's own clock); this
   * label is decoration computed against the *browser's* clock, so it defers to the
   * shared skew-tolerant `formatSeenAgo` (`when.ts`) rather than re-deriving its own
   * tolerance window.
   */
  protected seenLabel(row: RunnerRow): string {
    return formatSeenAgo(row.last_seen_at, row.online, row.nowMs);
  }
}
