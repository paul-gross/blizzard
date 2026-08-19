import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { MeResponse } from '../api/hub';
import { KitButton } from '../kit';

/**
 * The `pending` lobby (issue #93; renamed from the `guest` lobby by issue #210) — an
 * authenticated user resolved with an **empty** permission set (a freshly-linked
 * account, `role = "pending"`, before an admin grants a role — #94's role
 * assignment) sees this instead of the board: "signed in, awaiting access", not a
 * board silently failing every gated read with `403`s. Presentational: the app root
 * decides *when* to render this (an `authState` of `'lobby'`) and hands down the
 * resolved identity; logout is a working control here too (the AC: "a pending
 * account can log out from the lobby") — this only emits the intent, the container
 * owns the mutation.
 */
@Component({
  selector: 'fleet-pending-lobby',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './pending-lobby.html',
  styleUrl: './pending-lobby.css',
})
export class PendingLobby {
  /** The resolved identity — always non-`null` while this renders (the app root only
   * shows the lobby once `/api/me` resolved authenticated-but-permissionless). */
  readonly me = input<MeResponse | null>(null);

  /** Fired when the operator clicks "Log out"; the container owns the mutation. */
  readonly logout = output<void>();
}
