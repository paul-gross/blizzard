import { ChangeDetectionStrategy, Component, input, type TemplateRef } from '@angular/core';
import { RouterLink } from '@angular/router';
import { type EventView, type KitAsyncStateValue, KitAsyncState, KitFactList, type KitFact, KitPanel, KitSkeleton, formatAbsolute, runnerDisplayName } from 'fleet';
import { AppLink } from 'fleet/shell';

import { fleetIdOf, type HostMembership } from '../core/runners.domain';

@Component({
  selector: 'app-event-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitFactList, KitPanel, KitSkeleton, AppLink, RouterLink],
  templateUrl: './event-detail-view.html',
  styleUrl: './event-detail-view.css',
})
export class EventDetailView {
  readonly event = input<EventView | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());
  /** The hosts and the runners each lists — what the runner link resolves against. */
  readonly hosts = input<readonly HostMembership[]>([]);
  readonly mobile = input(false);
  protected runnerName(id: string, name: string | null): string {
    return runnerDisplayName(id, name, this.runnerHosts().get(id));
  }
  protected fleetId(id: string): string { return fleetIdOf(id, this.hosts()); }
  /** The detail's rows: the kind, severity, message and instant as text, the optional ids as text, the links and payload as markup. */
  protected facts(ev: EventView, runner: TemplateRef<unknown>, chunk: TemplateRef<unknown>, payload: TemplateRef<unknown>): readonly KitFact[] {
    const rows: KitFact[] = [
      { label: 'Kind', value: ev.kind },
      { label: 'Severity', value: ev.severity },
      { label: 'Message', value: ev.message },
      { label: 'Recorded at', value: formatAbsolute(ev.recorded_at) },
    ];
    if (ev.runner_id) rows.push({ label: 'Runner', template: runner });
    if (ev.chunk_id) rows.push({ label: 'Chunk', template: chunk });
    if (ev.lease_id) rows.push({ label: 'Lease', value: ev.lease_id });
    if (ev.node_name) rows.push({ label: 'Node', value: ev.node_name });
    if (ev.detail) rows.push({ label: 'Detail', template: payload });
    return rows;
  }
  protected payload(event: EventView): string { return JSON.stringify(event.detail, null, 2); }
}
