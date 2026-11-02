import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { filter, map } from 'rxjs';
import { KitMenuItem, KitMenuPanel, MobileTitlebar as FleetMobileTitlebar, ViewportMenu, formatCost, type FleetSpendView } from 'fleet';
import { KitSwatch, LensLink } from 'fleet/shell';

import { injectLensedProject } from '../../core/lensed-project';
import { unlensedUrl } from '../../core/project-lens';
import { LENS_PICKER_PATH, lensPickerFrom } from '../lens-picker/lens-picker.model';
import { TenantMenu } from './tenant-menu';
import { injectTenantSwitch } from './tenant-switch';

/**
 * The hub's mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the app root's mobile
 * counterpart to the desktop {@link BoardHeader}/{@link AppNav} pair.
 *
 * A thin wrapper around the shared {@link FleetMobileTitlebar}. This
 * layer only supplies the hub's own live signal (`FleetLiveUpdates`) and the
 * shell menu's panel — the appearance switcher (a CDK-menu
 * submenu), declared here rather than projected
 * because a `CdkMenu` cannot see items across an `<ng-content>` boundary.
 * No board/graphs tabs here; navigation lives in the
 * bottom {@link MobileTabBar}. The fleet component's default `testid`
 * (`'mobile-titlebar'`) is used as-is.
 *
 * Beside the wordmark the bar names the project lens — the lensed project, or All projects — and tapping it opens the
 * lens picker subscreen ({@link LensPicker}), the mobile counterpart of the desktop tab row's lens control, carrying
 * the view the board was on so the picker returns there. Mobile has no profile menu, so a caller with several
 * memberships finds the tenant switcher in the menu, the same {@link TenantMenu} submenu.
 */
@Component({
  selector: 'app-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, FleetMobileTitlebar, KitMenuItem, KitMenuPanel, KitSwatch, LensLink, RouterLink, TenantMenu, ViewportMenu],
  templateUrl: './mobile-titlebar.html',
  styleUrl: './mobile-titlebar.css',
})
export class MobileTitlebar {
  /** Whether the SSE spine is currently connected — the same `FleetLiveUpdates`
   * state (`app.ts`'s `streamLive`) the desktop titlebar's own "Hub" cell derives
   * its own connection string from. */
  readonly live = input.required<boolean>();

  /** The fleet's spend-today read the desktop header's TODAY cell shows (`app.ts`'s `spendToday`), `null` before it
   * resolves — shown beside the live dot. */
  readonly spendToday = input<FleetSpendView | null>(null);

  protected readonly formatCost = formatCost;

  /** The memberships and the switch the tenant submenu offers. */
  protected readonly tenants = injectTenantSwitch();

  /** The project the lens is on, which the bar names — `null` on All. */
  protected readonly lensed = injectLensedProject();

  private readonly router = inject(Router);

  protected readonly lensName = computed(() => this.lensed()?.name ?? 'All projects');

  /** The picker, kept under the lens the board is on through `lensLink`. */
  protected readonly pickerLink = ['/', LENS_PICKER_PATH];

  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.router.url),
    ),
    { initialValue: this.router.url },
  );

  /** The unlensed view the board is on, for the picker to return to. */
  protected readonly fromView = computed(() => lensPickerFrom(unlensedUrl(this.url())));
}
