import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import {
  BoardHeader,
  FleetLiveUpdates,
  ViewportService,
  injectHubChunksQuery,
  injectHubFleetSpendQuery,
  injectHubHealthQuery,
} from 'fleet';

import { startOfLocalDayIso } from './local-day';
import { AppNav } from './nav/app-nav';
import { MobileTabBar } from './nav/mobile-tab-bar';
import { MobileTitlebar } from './nav/mobile-titlebar';

/**
 * The hub app shell — the titlebar, the top nav (or their mobile shell
 * counterparts), and the routed content.
 *
 * The window is a full-width titlebar ({@link BoardHeader} — the brand, the live
 * fleet counts, and the hub connection) over a tab strip ({@link AppNav}), with the
 * active route rendered below via `<router-outlet>` — desktop mode. In mobile mode
 * (`ViewportService.mode`) both are replaced by {@link MobileTitlebar}, and a
 * persistent {@link MobileTabBar} renders below the routed content (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — the fork happens once, here, at the
 * app root, the same "pick it once" rule the route table already follows for the
 * `board` route itself (`app.routes.ts`).
 *
 * - the {@link FleetLiveUpdates} spine subscribes to `GET /api/events/stream` and
 *   invalidates the reads on every hub fact, so the whole app streams live;
 * - both the SSE subscription and the TanStack `QueryClient` (the app-config
 *   singleton, see `app.config.ts`) are scoped to this root component, never to a
 *   routed page, so navigating between tabs never restarts the stream or drops the
 *   query cache.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardHeader, AppNav, MobileTitlebar, MobileTabBar, RouterOutlet],
  template: `
    <div class="layout">
      @if (mobile()) {
        <app-mobile-titlebar [live]="streamLive()" />
      } @else {
        <fleet-board-header [connection]="connection()" [chunks]="chunks()" [spendToday]="spendToday.data() ?? null" />
        <app-nav />
      }
      <router-outlet />
      @if (mobile()) {
        <app-mobile-tab-bar />
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    /* The titlebar and nav span the window, and the routed page fills everything
       under them. The layout is height-capped to the viewport and the routed page
       scrolls its own content, so the page itself never scrolls — an operator's
       board does not move under them. */
    .layout {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    /* router-outlet is an empty anchor element the router inserts routed
       components after — it carries no visual size of its own. */
    router-outlet {
      display: none;
    }
  `,
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly live = inject(FleetLiveUpdates);
  private readonly viewport = inject(ViewportService);

  /** The fleet's spend-today read (issue #60) — `since` is local start-of-day,
   * recomputed each time the query re-derives its key (a day rollover moves the
   * window forward, same as any other calendar-relative read). */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso());

  /** The app-root-level shell fork (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — picked once here, mirroring the
   * `board` route's own guard-based fork in `app.routes.ts`. */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  constructor() {
    // Open the SSE stream and wire it to the query cache for the app's lifetime.
    this.live.start();
  }

  /** Header status: the live stream's connection state, falling back to the health read. */
  protected readonly connection = computed(() => {
    const streamState = this.live.status();
    if (streamState === 'reconnecting') return 'reconnecting…';
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** Whether the SSE spine is currently connected — {@link MobileTitlebar}'s
   * live dot, the same `open` check the glance board's own vitals strip uses
   * (`board/glance/glance-board.ts`). */
  protected readonly streamLive = computed(() => this.live.status() === 'open');
}
