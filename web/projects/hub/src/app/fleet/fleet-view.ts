import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import {
  KitAsyncState,
  KitBadge,
  KitButton,
  KitPaceBar,
  KitPanel,
  KitSlotBar,
  STATUS_TONE,
  formatSeenAgo,
  localPauseHint,
  runnerToggleHint,
  type ChunkStatus,
  type KitAsyncStateValue,
  type RunnerRow,
  type Tone,
} from 'fleet';

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
  imports: [KitAsyncState, KitBadge, KitButton, KitPaceBar, KitPanel, KitSlotBar],
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

  /** The runner ids whose hub pause/resume mutation the container's shared
   * `pauseMutation` is currently in flight for — a plain id list rather than a
   * field folded onto {@link RunnerRow} itself, mirroring `RunnerPanelView`'s own
   * `pendingRunnerIds`. Scoping the toggle's `disabled` state to this list is what
   * keeps a sibling row's button enabled while only the row that was tapped
   * disables — the one `pauseMutation` instance fires once per row, so its bare
   * `isPending()` would freeze every row alike. */
  readonly pendingRunnerIds = input<readonly string[]>([]);

  /** The page's last pause/resume failure, or `null` — rendered as a visible inline
   * notice near the registry (issue #42's "report, don't swallow"). */
  readonly actionError = input<string | null>(null);

  /** Whether `row`'s own hub pause/resume mutation is in flight — the per-row
   * membership check against {@link pendingRunnerIds}. */
  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }

  /** Emitted with the row to flip the **hub** brake on. */
  readonly togglePause = output<RunnerRow>();

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
