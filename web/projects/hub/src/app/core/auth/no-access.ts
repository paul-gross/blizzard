import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type MeResponse, KitButton } from 'fleet/shell';

/**
 * The no-access screen — a signed-in person who holds no membership in any tenant sees this, and only this: no
 * header, no nav, no view. It renders outside the shell, because the shell always acts in a tenant and there is none
 * to act in. Presentational: hands down the resolved identity; logout only emits the intent, the container owns the
 * mutation.
 */
@Component({
  selector: 'app-no-access',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './no-access.html',
  styleUrl: './no-access.css',
})
export class NoAccess {
  /** The resolved identity — never `null` while this renders (the app root shows it once `/api/me` resolved). */
  readonly me = input<MeResponse | null>(null);

  /** Whether the logout is in flight; disables the button. */
  readonly logoutPending = input(false);

  /** The failed logout's message, `null` while none. */
  readonly logoutError = input<string | null>(null);

  /** Fired when the person clicks "Log out"; the container owns the mutation. */
  readonly logout = output<void>();
}
