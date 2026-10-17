import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue, KitMasterDetail, KitPanel, KitSelectRow, RunnerConnection, KitBeacon } from 'fleet';
import type { FleetList, FleetListCard, FleetSelection } from './fleet-page.model';
import { FleetRunnerRow } from './fleet-runner-row';
import { HostDetail } from './host-detail';
import type { HostVerbEvent, HostVerbsVm } from './host-verbs.model';
import { NO_HOST_VERBS } from './host-verbs.model';
import type { RunnerRow } from './runner-rows';

/**
 * The desktop Fleet tab's presentational half: host cards (then a "No host" group) beside
 * the selected host's detail. Fully controlled — the selection is the URL's, held by the
 * container; a pick only emits.
 */
@Component({
  selector: 'app-fleet-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon, NgTemplateOutlet, FleetRunnerRow, HostDetail, KitAsyncState, KitMasterDetail, KitPanel, KitSelectRow],
  templateUrl: './fleet-view.html',
  styleUrl: './fleet-view.css',
})
export class FleetView {
  readonly list = input.required<FleetList>();
  readonly selection = input<FleetSelection | null>(null);
  readonly selectedId = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly canPause = input(false);
  readonly pendingRunnerIds = input<readonly string[]>([]);
  readonly actionError = input<string | null>(null);
  readonly hostVerbs = input<HostVerbsVm>(NO_HOST_VERBS);

  readonly pick = output<string>();
  readonly hostVerb = output<HostVerbEvent>();
  readonly togglePause = output<RunnerRow>();

  protected readonly connection = RunnerConnection;

  protected agentsLabel(card: FleetListCard): string {
    return card.agents === null ? '' : `${card.agents} ${card.agents === 1 ? 'agent' : 'agents'}`;
  }

  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }
}
