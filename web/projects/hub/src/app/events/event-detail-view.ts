import { ChangeDetectionStrategy, Component, input, type TemplateRef } from '@angular/core';
import { RouterLink } from '@angular/router';
import { formatAbsolute, runnerDisplayName } from 'fleet/core';
import { type KitAsyncStateValue, KitAsyncState, KitFactList, type KitFact, KitPanel, KitSkeleton } from 'fleet/kit';
import { AppLink } from 'fleet/shell';

import { fleetIdOf, type HostMembership } from '../core/runners.domain';
import { eventSeverityText, type EventLogEntry } from './event-log.domain';

@Component({
  selector: 'app-event-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitFactList, KitPanel, KitSkeleton, AppLink, RouterLink],
  templateUrl: './event-detail-view.html',
  styleUrl: './event-detail-view.css',
})
export class EventDetailView {
  readonly event = input<EventLogEntry | null>(null);
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
  protected facts(ev: EventLogEntry, runner: TemplateRef<unknown>, chunk: TemplateRef<unknown>, payload: TemplateRef<unknown>): readonly KitFact[] {
    const rows: KitFact[] = [
      { label: 'Kind', value: ev.kind },
      { label: 'Severity', value: eventSeverityText(ev.severity) },
      { label: 'Message', value: ev.message },
      { label: 'Recorded at', value: formatAbsolute(ev.recordedAt) },
    ];
    if (ev.runnerId) rows.push({ label: 'Runner', template: runner });
    if (ev.chunkId) rows.push({ label: 'Chunk', template: chunk });
    if (ev.leaseId) rows.push({ label: 'Lease', value: ev.leaseId });
    if (ev.nodeName) rows.push({ label: 'Node', value: ev.nodeName });
    if (ev.detail) rows.push({ label: 'Detail', template: payload });
    return rows;
  }
  protected payload(event: EventLogEntry): string { return JSON.stringify(event.detail, null, 2); }
}
