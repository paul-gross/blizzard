import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu } from 'fleet';
import { injectRunnerDashboardQuery } from '../status/status.query';
import { injectRunnerSessionQuery, signedInUsername } from '../identity/auth.query';
import { LocalIdentity } from '../identity/app-identity';

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
 * component injecting the same dashboard/session queries `LocalPanel`'s other
 * rails (and `app-info`) also inject costs no extra network request.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, LocalIdentity, ViewportMenu],
  templateUrl: './mobile-titlebar.html',
})
export class MobileTitlebar {
  /** The titlebar's own severable read (`app-info.ts`'s own instance dedupes
   * on the same query key, so this is not a second poll) — `runner.hub.reachable`
   * off `GET /api/dashboard`, the same fact `app-info`'s "link" cell renders. */
  private readonly dashboardQuery = injectRunnerDashboardQuery();

  /** Whether the hub link is reachable — the titlebar's `live` dot. A
   * malformed body (e.g. a misrouted proxy) must degrade to `false`, not
   * throw mid-render — the same guard `app-info.ts`'s own `view` takes. */
  protected readonly hubReachable = computed(() => this.dashboardQuery.data()?.runner?.hub?.reachable ?? false);

  private readonly sessionQuery = injectRunnerSessionQuery();

  /** Whether a hub username is signed in — the gate on the titlebar menu's
   * `Log out` item. Read here, on a component that has been alive since the
   * shell mounted, rather than off the {@link LocalIdentity} inside the overlay,
   * so the item exists in the panel's very first change detection and
   * `CdkMenu` focuses it as the first item. */
  protected readonly signedIn = computed(() => signedInUsername(this.sessionQuery.data()) !== null);
}
