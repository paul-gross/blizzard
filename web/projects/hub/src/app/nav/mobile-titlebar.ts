import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { MobileTitlebar as FleetMobileTitlebar, ViewportToggle } from 'fleet';

/**
 * The hub's mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the app root's mobile
 * counterpart to the desktop {@link BoardHeader}/{@link AppNav} pair, picked
 * once in `App` (`../app.ts`) off `ViewportService.mode`, never a per-page
 * `@if` (the same route-table-level fork `matchesMobileViewport` already
 * uses for the board route).
 *
 * A thin wrapper around the shared {@link FleetMobileTitlebar} (issue #92) —
 * the runner's `local-panel-mobile.ts` mounts the same fleet component. This
 * layer only supplies the hub's own live signal (`FleetLiveUpdates`, the same
 * connection state the desktop titlebar's own "Hub" cell derives from) and
 * projects the viewport-override menu (item 5) into the shared component's
 * menu slot — deliberately **no** board/graphs tabs: mobile navigation lives
 * in the bottom {@link MobileTabBar} instead (mock screen C's `.tabbar`),
 * never here. The fleet component defaults its own `testid` to
 * `'mobile-titlebar'`, so this wrapper needs no input to keep the hub's
 * existing `mobile-titlebar*` handles working unchanged.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetMobileTitlebar, ViewportToggle],
  template: `
    <fleet-mobile-titlebar [live]="live()">
      <fleet-viewport-toggle />
    </fleet-mobile-titlebar>
  `,
})
export class MobileTitlebar {
  /** Whether the SSE spine is currently connected — the same `FleetLiveUpdates`
   * state (`app.ts`'s `streamLive`) the desktop titlebar's own "Hub" cell derives
   * its own connection string from. */
  readonly live = input.required<boolean>();
}
