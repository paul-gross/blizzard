import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router } from '@angular/router';
import { EventsPanel } from 'fleet';

/**
 * The `/events` route (blizzard#125 Phase 4) — the board's Events tab: the hub's
 * persisted operational event feed (`GET /api/events`), filterable by
 * severity/runner/chunk, in one full-page panel. Composes {@link EventsPanel} the
 * way `graphs-page.ts`
 * composes `GraphExplorer`/`GraphDetail`: the page owns only the route-level
 * concern (here, opening a chunk elsewhere) and leaves the query and filter state
 * to the panel itself.
 *
 * Activating a row's chunk deep-links straight to it: `/board?chunk=<id>`, the
 * query param {@link BoardPage} reads its selection from (issue #162). The feed
 * names a chunk id on every row, so the operator lands on the board with that
 * chunk already open in the dock rather than having to find it among the lanes.
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

  /** Open one chunk on the board, selected — the same `chunk` param a board card
   * writes, so arriving from the feed and clicking a card land in one state. */
  protected openChunk(chunkId: string): void {
    void this.router.navigate(['/board'], { queryParams: { chunk: chunkId } });
  }
}
