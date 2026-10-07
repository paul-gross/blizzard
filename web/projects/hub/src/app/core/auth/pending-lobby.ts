import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type MeResponse, KitButton } from 'fleet/shell';

/**
 * The `pending` lobby — an
 * authenticated user resolved with an **empty** permission set (a freshly-linked
 * account, `role = "pending"`, before an admin grants a role) sees this instead of
 * the board: "signed in, awaiting access", not a
 * board silently failing every gated read with `403`s. Presentational: hands down
 * the resolved identity; logout is a working control here — this only emits the
 * intent, the container owns the mutation.
 */
@Component({
  selector: 'app-pending-lobby',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './pending-lobby.html',
  styleUrl: './pending-lobby.css',
})
export class PendingLobby {
  /** The resolved identity — always non-`null` while this renders (the app root only
   * shows the lobby once `/api/me` resolved authenticated-but-permissionless). */
  readonly me = input<MeResponse | null>(null);

  /** Whether the logout is in flight; disables the button. */
  readonly logoutPending = input(false);

  /** The failed logout's message, `null` while none. */
  readonly logoutError = input<string | null>(null);

  /** Fired when the operator clicks "Log out"; the container owns the mutation. */
  readonly logout = output<void>();
}
