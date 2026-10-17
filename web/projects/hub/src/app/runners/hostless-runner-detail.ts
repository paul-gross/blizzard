import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { KitAsyncState, KitBadge, KitBeacon, KitFactList, KitPanel, KitSlotBar, RunnerConnection, SubscriptionPaceGroup } from 'fleet';
import type { RunnerDetailVm } from './fleet-page.model';
import { FleetRunnerRow } from './fleet-runner-row';
import { connectionState } from './host-rows.model';
import { runnerSeenLabel, type RunnerRow } from './runner-rows';

/**
 * The Fleet tab's right pane for a runner standing under no host — the host detail's shape
 * for a machine of one: the title bar, then its Workspace (with its env slots), its
 * Subscription usage, and the runner's own row. No host carries its pool or spend, so this
 * pane does.
 *
 * Presentational only: the container owns the permission, the pending set and the mutation.
 */
@Component({
  selector: 'app-hostless-runner-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunnerRow, KitAsyncState, KitBadge, KitBeacon, KitFactList, KitPanel, KitSlotBar, SubscriptionPaceGroup],
  templateUrl: './hostless-runner-detail.html',
  styleUrls: ['./fleet-detail.css', './hostless-runner-detail.css'],
})
export class HostlessRunnerDetail {
  readonly detail = input.required<RunnerDetailVm>();
  readonly canPause = input(false);
  readonly pausePending = input(false);

  readonly togglePause = output<RunnerRow>();

  protected readonly connection = RunnerConnection;
  protected readonly seenLabel = runnerSeenLabel;

  protected readonly state = computed(() => connectionState(this.detail().row.online, this.detail().row.connection));

  /** "1 / 2 slots" — only when the runner reported a capacity, so one registered by a
   * client that predates the field gets no misleading zero-slot bar. */
  protected readonly slotsLabel = computed(() => {
    const { used, env_capacity: capacity } = this.detail().row;
    return capacity === null || capacity === undefined ? null : `${used} / ${capacity} slots`;
  });
}
