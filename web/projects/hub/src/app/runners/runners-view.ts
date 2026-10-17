import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitAsyncState, KitBadge, KitButton, KitChip, KitPanel, KitSlotBar, RunnerConnection, STATUS_TONE, SubscriptionPaceGroup, type ChunkStatus, type KitAsyncStateValue, type Tone, KitBeacon } from 'fleet';
import { HostCardView } from './host-card';
import { NO_HOST_VERBS, type HostVerbEvent, type HostVerbsVm } from './host-verbs.model';
import { groupHostRows, type HostCard, type HostGroups } from './host-rows.model';
import { ServedProjectGroup } from './served-project-group';
import { localPauseHint, runnerSeenLabel, runnerToggleHint, type RunnerRow } from './runner-rows';

/**
 * The mobile Fleet screen's presentational half: a phone-width, full-width
 * stacked card per runner — identity, liveness dot + seen label, workspace,
 * current claims, the env-slot bar, rate-limit pace bars, both pause brakes,
 * and the hub pause/resume toggle. Renders exactly the rows it is handed;
 * injects no query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'app-runners-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon, HostCardView, NgTemplateOutlet, KitAsyncState, KitBadge, KitButton, KitChip, KitPanel, KitSlotBar, ServedProjectGroup, SubscriptionPaceGroup],
  templateUrl: './runners-view.html',
  styleUrl: './runners-view.css',
})
export class RunnersView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The host cards the rows group under — a row whose host has no card renders under the
   * host-less heading, as every row does when there are no cards. */
  readonly cards = input<readonly HostCard[]>([]);

  /** The host verbs' permission, pending state, refusal and enrolled token, for each card. */
  readonly hostVerbs = input<HostVerbsVm>(NO_HOST_VERBS);

  /** A confirmed host verb from one of the cards, for the container to run. */
  readonly hostVerb = output<HostVerbEvent>();

  /** {@link rows} partitioned under {@link cards}. */
  protected readonly groups = computed<HostGroups<RunnerRow>>(() => groupHostRows(this.rows(), this.cards()));

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

  protected readonly seenLabel = runnerSeenLabel;

  /** The wire's connection vocabulary, for the row's connection classes and badge. */
  protected readonly connection = RunnerConnection;

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
