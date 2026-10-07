import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router } from '@angular/router';
import { ViewportService } from 'fleet';
import { EventsPanel } from './events-panel';

/**
 * The `/events` route — the board's Events tab: the hub's
 * persisted operational event feed (`GET /api/events`), filterable by
 * severity/runner/chunk, in one full-page panel. Composes {@link EventsPanel} the
 * way `graphs-page.ts`
 * composes `GraphExplorer`/`GraphDetail`: the page owns only the route-level
 * concern (here, opening a chunk elsewhere) and leaves the query and filter state
 * to the panel itself.
 *
 * Activating a row's chunk opens the selected board dock on desktop and the
 * chunk detail route on mobile, where the board shows a glance instead of lanes.
 */
@Component({
  selector: 'app-events-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsPanel],
  templateUrl: './events-page.html',
  styleUrl: './events-page.css',
})
export class EventsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);

  /** Open the selected chunk in the shell the current viewport actually renders. */
  protected openChunk(chunkId: string): void {
    if (this.viewport.mode() === 'mobile') {
      void this.router.navigate(['/board', 'chunk', chunkId]);
    } else {
      void this.router.navigate(['/board'], { queryParams: { chunk: chunkId } });
    }
  }
}
