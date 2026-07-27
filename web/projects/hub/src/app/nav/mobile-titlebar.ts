import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu } from 'fleet';

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
 * connection state the desktop titlebar's own "Hub" cell derives from) and the
 * shell menu's panel — the appearance switcher (item 5), now the CDK-menu
 * submenu the shells share (issue #161), declared here rather than projected
 * because a `CdkMenu` cannot see items across an `<ng-content>` boundary.
 * Deliberately **no** board/graphs tabs: mobile navigation lives
 * in the bottom {@link MobileTabBar} instead (mock screen C's `.tabbar`),
 * never here. The fleet component defaults its own `testid` to
 * `'mobile-titlebar'`, so this wrapper needs no input to keep the hub's
 * existing `mobile-titlebar*` handles working unchanged.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, ViewportMenu],
  template: `
    <fleet-mobile-titlebar [live]="live()" [menu]="shellMenu" />
    <ng-template #shellMenu>
      <fleet-kit-menu-panel testid="mobile-titlebar-menu-panel">
        <fleet-kit-menu-item testid="mobile-titlebar-appearance" submenu [cdkMenuTriggerFor]="appearanceMenu">
          Appearance
        </fleet-kit-menu-item>
      </fleet-kit-menu-panel>
    </ng-template>
    <ng-template #appearanceMenu>
      <fleet-viewport-menu testid="mobile-titlebar-appearance-panel" />
    </ng-template>
  `,
})
export class MobileTitlebar {
  /** Whether the SSE spine is currently connected — the same `FleetLiveUpdates`
   * state (`app.ts`'s `streamLive`) the desktop titlebar's own "Hub" cell derives
   * its own connection string from. */
  readonly live = input.required<boolean>();
}
