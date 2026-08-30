import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The graph detail panel's own header content — the lifecycle badge and graph id
 * that supplement `fleet-kit-panel`'s `label` (bound to the graph's name directly
 * in `graph-detail.html`) in its header bar. Projected into `KitPanel`'s
 * `fleetKitPanelHeader` slot in supplement mode: `:host`'s `display: contents`
 * (mirroring `MachineDetailHeader`'s own convention) puts both spans directly
 * alongside the panel's own `.lbl` as flex items of its `.p-hdr`, painting no
 * chrome of its own (`bzh:frontend-kit-floor`) — the retire/enable controls, the
 * action-error line, and the entry-node line are `GraphDetailLifecycle`'s,
 * rendered as ordinary body content below the header bar rather than inside it.
 */
@Component({
  selector: 'fleet-graph-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-detail-header.html',
  styleUrl: './graph-detail-header.css',
})
export class GraphDetailHeader {
  readonly graphId = input.required<string>();
  readonly retired = input.required<boolean>();
}
