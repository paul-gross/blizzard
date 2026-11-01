import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue, KitMasterDetail, KitPanel, KitSelectRow, KitErrorLine } from 'fleet';
import type { FleetList, FleetSelection } from './fleet-page.model';
import { HostDetail } from './host-detail';
import { HostlessRunnerDetail } from './hostless-runner-detail';
import type { HostVerbEvent, HostVerbsVm } from './host-verbs.model';
import { NO_HOST_VERBS } from './host-verbs.model';
import type { RunnerRow } from './runner-rows.model';
import { StatusLight } from './status-light';

/**
 * The desktop Fleet tab's presentational half: host cards (then a "No host" group) beside
 * the selected host's detail. Fully controlled — the selection is the URL's, held by the
 * container; a pick only emits.
 */
@Component({
  selector: 'app-fleet-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostDetail, HostlessRunnerDetail, KitAsyncState, KitMasterDetail, KitPanel, KitSelectRow, NgTemplateOutlet, StatusLight, KitErrorLine],
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

  /** The detail panel's header: what kind of thing is open. */
  protected readonly detailLabel = computed(() => {
    const sel = this.selection();
    if (sel === null) return null;
    return sel.kind === 'host' ? 'Host' : sel.kind === 'runner' ? 'Runner · no host' : 'Fleet';
  });

  /** The detail panel's trailing header value: the open card's short ref. */
  protected readonly detailRef = computed(() => {
    const sel = this.selection();
    return sel === null || sel.kind === 'not-found' ? null : sel.ref;
  });

  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }
}
