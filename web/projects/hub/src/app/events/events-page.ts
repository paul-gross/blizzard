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
 * No chunk deep-link target exists on the board today (`selected` is local
 * component state on `BoardPage`, not route- or query-param-driven), so activating
 * a row's chunk navigates to `/board` — the surface where every chunk lives —
 * rather than a param the board doesn't yet read.
 */
@Component({
  selector: 'app-events-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsPanel],
  template: `
    <div class="layout">
      <fleet-events-panel class="feed" (selectChunk)="openChunk()" />
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .layout {
      height: 100%;
      min-height: 0;
      padding: 6px;
      display: flex;
    }
    .feed {
      flex: 1;
      min-height: 0;
      min-width: 0;
    }
  `,
})
export class EventsPage {
  private readonly router = inject(Router);

  protected openChunk(): void {
    void this.router.navigate(['/board']);
  }
}
