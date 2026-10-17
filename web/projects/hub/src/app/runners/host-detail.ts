import { ChangeDetectionStrategy, Component, type TemplateRef, computed, input, output, viewChild } from '@angular/core';

import { KitAsyncState, KitBadge, KitBeacon, type KitFact, KitFactList, KitPanel, RunnerConnection, SubscriptionPaceGroup } from 'fleet';
import { FleetRunnerRow } from './fleet-runner-row';
import type { HostDetailVm } from './fleet-page.model';
import { connectionState } from './host-rows.model';
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
  imports: [FleetRunnerRow, HostVerbBar, KitAsyncState, KitBadge, KitBeacon, KitFactList, KitPanel, SubscriptionPaceGroup],
  templateUrl: './host-detail.html',
  styleUrls: ['./fleet-detail.css', './host-detail.css'],
})
export class HostDetail {
  readonly detail = input.required<HostDetailVm>();
  readonly verbs = input<HostVerbsVm>(NO_HOST_VERBS);
  readonly canPause = input(false);
  readonly pendingRunnerIds = input<readonly string[]>([]);

  readonly hostVerb = output<HostVerbEvent>();
  readonly togglePause = output<RunnerRow>();

  protected readonly connection = RunnerConnection;

  private readonly lastReport = viewChild.required<TemplateRef<unknown>>('lastReport');
  private readonly token = viewChild.required<TemplateRef<unknown>>('token');
  private readonly agents = viewChild.required<TemplateRef<unknown>>('agents');

  /** Connection's first column: the last report over the host's record. */
  protected readonly connectionRecord = computed<readonly KitFact[]>(() => [
    { label: 'Last report', template: this.lastReport(), testid: 'fleet-fact-last-report' },
    ...this.detail().facts,
  ]);

  /** Connection's second column: how the host is reached, its token, and its agents. */
  protected readonly connectionLive = computed<readonly KitFact[]>(() => [
    { label: 'URL', value: this.detail().card.url ?? '—', testid: 'fleet-fact-url' },
    { label: 'Token', template: this.token(), testid: 'fleet-fact-token' },
    { label: 'Agents', template: this.agents(), testid: 'fleet-fact-agents' },
  ]);

  protected readonly state = computed(() => connectionState(this.detail().card.online, this.detail().card.connection));

  /** "reported 4s ago", or "never reported" — nothing when a report's age can't be read, since
   * the Last report fact still states its instant. */
  protected readonly reportText = computed(() => {
    const card = this.detail().card;
    if (card.connection === RunnerConnection.NEVER_CONNECTED) return 'never reported';
    return card.reportedAgo === null ? null : `reported ${card.reportedAgo}`;
  });

  protected readonly envsLabel = computed(() => {
    const { envsInUse, envsCapacity, workspaces } = this.detail();
    if (workspaces.length === 0) return null;
    return envsCapacity === null ? `${envsInUse} in use` : `${envsInUse} / ${envsCapacity} in use`;
  });

  protected isPausePending(row: RunnerRow): boolean {
    return this.pendingRunnerIds().includes(row.runner_id);
  }
}
