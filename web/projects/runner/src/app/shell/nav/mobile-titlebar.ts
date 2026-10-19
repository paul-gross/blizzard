import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu } from 'fleet';
import { injectPanelDashboards } from '../../core/panel-reads';
import { anyHubReachable } from '../../core/panel-reads.model';
import { injectRunnerSessionQuery, signedInUsername } from '../../core/identity/auth.query';
import { LocalIdentity } from '../../core/identity/app-identity';
import { RunnerLogout } from '../../core/identity/runner-logout';

/**
 * The runner's mobile titlebar sits at the app root (`../app.ts`),
 * where `AppShell` (`fleet`) keeps it above the routed content on every route.
 *
 * A thin wrapper around the shared {@link FleetMobileTitlebar},
 * mirroring the hub's own wrapper in shape: this layer supplies the runner's
 * own hub-reachability read as the live signal and declares the shell menu's
 * panel (a `CdkMenu` cannot see items projected across an `<ng-content>`
 * boundary, so the panel has to be declared here rather than passed through).
 * Unlike the hub's wrapper, this one also carries the signed-in identity row
 * and its `Log out` item, since the
 * runner (unlike the hub) has its own local session concept independent of
 * the hub's.
 *
 * TanStack dedupes query-key injections, so this
 * component injecting the same dashboard/session queries the board's rail
 * (its hub panel) injects costs no extra network request.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, LocalIdentity, ViewportMenu],
  templateUrl: './mobile-titlebar.html',
  styleUrl: './mobile-titlebar.css',
})
export class MobileTitlebar {
  /** The titlebar's own severable read (the hub panel's dedupes on the same query keys, so
   * this is not a second poll) — each covered runner's `runner.hub.reachable` off
   * `GET /api/dashboard`, the same fact the hub panel's link reading folds. */
  private readonly dashboards = injectPanelDashboards();

  /** Whether any covered runner reaches its hub — the titlebar's `live` dot. A
   * malformed body (e.g. a misrouted proxy) must degrade to `false`, not
   * throw mid-render. */
  protected readonly hubReachable = computed(() => anyHubReachable(this.dashboards.data()));

  /** The shell's one logout owner: the menu item's pending flag and the error rendered beside the titlebar. */
  protected readonly logout = inject(RunnerLogout);

  private readonly sessionQuery = injectRunnerSessionQuery();

  /** Whether a hub username is signed in — the gate on the titlebar menu's
   * `Log out` item. Read here, on a component that has been alive since the
   * shell mounted, rather than off the {@link LocalIdentity} inside the overlay,
   * so the item exists in the panel's very first change detection and
   * `CdkMenu` focuses it as the first item. */
  protected readonly signedIn = computed(() => signedInUsername(this.sessionQuery.data()) !== null);
}
