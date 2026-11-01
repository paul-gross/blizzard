import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type MeResponse, type MeTenant, KitButton, KitErrorLine } from 'fleet/shell';

/**
 * The tenant picker — a person with several memberships, none of them this tab's tenant or the browser's last
 * choice, chooses the tenant the board opens in. It renders outside the shell, before any view: the shell always acts
 * in a tenant. Each tenant shows its id beside its name, since two tenants may share a name. Presentational: it emits
 * the chosen tenant's id and the logout intent; the container enters the tenant and owns the mutation.
 */
@Component({
  selector: 'app-tenant-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine],
  templateUrl: './tenant-picker.html',
  styleUrl: './tenant-picker.css',
})
export class TenantPicker {
  /** The memberships to choose among, as `/api/me` lists them. */
  readonly tenants = input.required<readonly MeTenant[]>();

  /** The resolved identity — never `null` while this renders. */
  readonly me = input<MeResponse | null>(null);

  /** Whether the logout is in flight; disables the button. */
  readonly logoutPending = input(false);

  /** The failed logout's message, `null` while none. */
  readonly logoutError = input<string | null>(null);

  /** Fired with the chosen tenant's id. */
  readonly choose = output<string>();

  /** Fired when the person clicks "Log out"; the container owns the mutation. */
  readonly logout = output<void>();
}
