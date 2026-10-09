import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitMenuItemRadio, KitMenuPanel } from 'fleet';

import type { MeTenant } from 'fleet/shell';

/**
 * The tenant switcher's submenu — one radio item per membership, the tenant the board is in checked. Each item shows
 * the tenant's name with its id beside it, since two tenants may share a name.
 *
 * Presentational only: {@link choose} hands the chosen tenant id to the menu that opened it, which switches.
 */
@Component({
  selector: 'app-tenant-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenuItemRadio, KitMenuPanel],
  templateUrl: './tenant-menu.html',
  styleUrl: './tenant-menu.css',
})
export class TenantMenu {
  /** The memberships to choose between. */
  readonly tenants = input.required<readonly MeTenant[]>();

  /** The id of the tenant the board is in, `null` for none. */
  readonly current = input<string | null>(null);

  /** The submenu panel's `data-testid`; each item's handle derives from it. */
  readonly testid = input.required<string>();

  /** Fired with the chosen tenant's id. */
  readonly choose = output<string>();
}
