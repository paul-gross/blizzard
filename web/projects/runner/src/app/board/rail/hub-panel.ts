import { ChangeDetectionStrategy, Component, computed, effect, signal } from '@angular/core';
import { asyncState, FleetWhen, KitAsyncState, type runnerApi, STATUS_TONE, toneColor } from 'fleet';

import { injectPanelDashboards } from '../../core/panel-reads';
import { hubSummary, latestFleetSummary } from './hub-panel.model';

/**
 * The board rail's hub panel — the hub this host's runners report to, read at a glance: its
 * endpoint and a link to its board, whether every runner reaches it and when one last did,
 * the hub's fleet counts once, and a line for each runner whose link needs the operator.
 * What each runner does on this host is the host panel's.
 *
 * The fleet counts are the hub's, the same through every runner, so they are read off the
 * first dashboard that carries them and latched: a hub outage is a successful read carrying
 * `fleet_summary: null`, and the strip then dims to its last-known counts rather than blank.
 */
@Component({
  selector: 'app-hub-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState],
  templateUrl: './hub-panel.html',
  styleUrl: './hub-panel.css',
})
export class HubPanel {
  private readonly dashboards = injectPanelDashboards('all');

  protected readonly summary = computed(() => hubSummary(this.dashboards.data()));

  private readonly currentFleet = computed<runnerApi.FleetSummaryView | null>(
    () => latestFleetSummary(this.dashboards.data()),
  );

  private readonly latchedFleet = signal<runnerApi.FleetSummaryView | null>(null);
  private readonly latchFleet = effect(() => {
    const fleet = this.currentFleet();
    if (fleet !== null) this.latchedFleet.set(fleet);
  });

  protected readonly fleet = this.latchedFleet.asReadonly();

  /** No runner's read carries the counts now — the strip shows the last known, dimmed. */
  protected readonly fleetStale = computed(() => this.currentFleet() === null);

  /** Each fleet-count bucket's colour, from the chunk status it counts. */
  protected readonly bucketColor = {
    ready: toneColor(STATUS_TONE.ready),
    running: toneColor(STATUS_TONE.running),
    waiting: toneColor(STATUS_TONE.waiting_on_human),
    needs: toneColor(STATUS_TONE.needs_human),
  } as const;

  protected readonly triadState = computed(() => asyncState(this.dashboards, this.summary() === null));
}
