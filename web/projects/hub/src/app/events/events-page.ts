import { ChangeDetectionStrategy, Component } from '@angular/core';
import { EventsPanel } from './events-panel';

/**
 * The `/events` route — the board's Events tab: the hub's
 * persisted operational event feed (`GET /api/events`), filterable by
 * severity/runner/chunk. Leaves its query and filter state to {@link EventsPanel}.
 */
@Component({
  selector: 'app-events-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsPanel],
  templateUrl: './events-page.html',
  styleUrl: './events-page.css',
})
export class EventsPage {}
