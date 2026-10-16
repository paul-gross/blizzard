import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitBadge, KitFactList, KitPanel, RunnerConnection, SubscriptionPaceGroup } from 'fleet';
import { FleetRunnerRow } from './fleet-runner-row';
import type { HostDetailVm } from './fleet-page.model';
import { HostVerbBar } from './host-verb-bar';
import { type HostVerbEvent, type HostVerbsVm, NO_HOST_VERBS } from './host-verbs.model';
import type { RunnerRow } from './runner-rows';

/**
 * The Fleet tab's right pane for one host: the title bar (light, name, short ref, state,
 * last report, and the host verbs), then Connection, Workspaces · environments,
 * Subscription usage and Runners, in that order.
 *
 * Presentational only: the container folds {@link HostDetailVm} and runs the verbs and
 * the pause brake.
 */
@Component({
  selector: 'app-host-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunnerRow, HostVerbBar, KitBadge, KitFactList, KitPanel, SubscriptionPaceGroup],
  templateUrl: './host-detail.html',
  styleUrl: './host-detail.css',
})
export class HostDetail {
  readonly detail = input.required<HostDetailVm>();
  readonly verbs = input<HostVerbsVm>(NO_HOST_VERBS);
  readonly canPause = input(false);
  readonly pendingRunnerIds = input<readonly string[]>([]);

  readonly hostVerb = output<HostVerbEvent>();
  readonly togglePause = output<RunnerRow>();

  protected readonly connection = RunnerConnection;

  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }
}
