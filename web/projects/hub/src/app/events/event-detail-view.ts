import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { type EventView, type KitAsyncStateValue, KitAsyncState, KitPanel, KitSkeleton, runnerDisplayName } from 'fleet';
import { AppLink } from 'fleet/shell';

@Component({
  selector: 'app-event-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, KitSkeleton, AppLink, RouterLink],
  templateUrl: './event-detail-view.html',
  styleUrl: './event-detail-view.css',
})
export class EventDetailView {
  readonly event = input<EventView | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());
  readonly runnerFleetIds = input<ReadonlyMap<string, string>>(new Map());
  readonly mobile = input(false);
  protected runnerName(id: string, name: string | null): string {
    return runnerDisplayName(id, name, this.runnerHosts().get(id));
  }
  protected payload(event: EventView): string { return JSON.stringify(event.detail, null, 2); }
}
