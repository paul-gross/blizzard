import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, KitBadge, KitButton, KitChip, KitPanel, KitSlotBar, STATUS_TONE, formatSeenAgo, type ChunkStatus, type KitAsyncStateValue, type Tone } from 'fleet';
import { SubscriptionPaceGroup } from './subscription-pace-group';
import { localPauseHint, runnerToggleHint, type RunnerRow } from './runner-rows';

/**
 * The mobile Fleet screen's presentational half: a phone-width, full-width
 * stacked card per runner — identity, liveness dot + seen label, workspace,
 * current claims, the env-slot bar, rate-limit pace bars, both pause brakes,
 * and the hub pause/resume toggle. Renders exactly the rows it is handed;
 * injects no query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'app-fleet-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitChip, KitPanel, KitSlotBar, SubscriptionPaceGroup],
  templateUrl: './fleet-view.html',
  styleUrl: './fleet-view.css',
})
export class FleetView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The registry query's async state — loading/error withhold the empty copy
   * until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether to render the hub pause/resume brake — withheld for an identity
   * without `runner:pause` (admin-tier). */
  readonly canPause = input(false);

  /** The runner ids with a pause/resume currently in flight — only those rows'
   * toggles disable. */
  readonly pendingRunnerIds = input<readonly string[]>([]);

  /** The page's last pause/resume failure, or `null` — rendered as a visible inline
   * notice near the registry ("report, don't swallow"). */
  readonly actionError = input<string | null>(null);

  /** Whether `row`'s own hub pause/resume mutation is in flight — the per-row
   * membership check against {@link pendingRunnerIds}. */
  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }

  /** Emitted with the row to flip the **hub** brake on. */
  readonly togglePause = output<RunnerRow>();

  /** Whether retired runners are listed — the container owns the flag (it drives the read). */
  readonly showRetired = input(false);

  /** Emitted when the "show retired" chip is clicked. */
  readonly toggleShowRetired = output<void>();

  protected readonly formatSeenAgo = formatSeenAgo;

  /** A claim's badge tone, read straight off `chunk-lanes.ts`'s `STATUS_TONE`
   * (`bzh:frontend-formatters`). */
  protected toneFor(status: ChunkStatus): Tone {
    return STATUS_TONE[status];
  }

  /** Whether to render the env-slot bar: only when the runner reported a
   * capacity — a runner registered by a client that predates the field gets
   * no bar, rather than a misleading zero-slot one. */
  protected hasCapacity(row: RunnerRow): boolean {
    return row.env_capacity !== null && row.env_capacity !== undefined;
  }

  protected readonly localPauseHint = localPauseHint;

  protected readonly toggleHint = runnerToggleHint;
}
