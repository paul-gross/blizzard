import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitPanel } from 'fleet';
import { FactLog } from './fact-log';

/**
 * The `events` route — the fact log of the runners the filters leave, at full width.
 * {@link FactLog} is self-fetching
 * (each runner's shared dashboard read), so this page owns nothing
 * beyond mounting it in a full-height panel, mirroring the hub's own
 * `events-page.ts`.
 */
@Component({
  selector: 'app-events-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FactLog, KitPanel],
  templateUrl: './events-page.html',
  styleUrl: './events-page.css',
})
export class EventsPage {}
