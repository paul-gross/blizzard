import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { MeResponse } from '../api/hub';
import { KitButton } from '../kit';

/**
 * The `guest` lobby (issue #93) — an authenticated user resolved with an **empty**
 * permission set (a freshly-linked account, `role = "guest"`, before an admin grants
 * anything — #94's role assignment) sees this instead of the board: "signed in,
 * awaiting access", not a board silently failing every gated read with `403`s.
 * Presentational: the app root decides *when* to render this (an `authState` of
 * `'lobby'`) and hands down the resolved identity; logout is a working control here
 * too (the AC: "a guest can log out from the lobby") — this only emits the intent,
 * the container owns the mutation.
 */
@Component({
  selector: 'fleet-guest-lobby',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    <div class="lobby" data-testid="guest-lobby">
      <p class="headline">Signed in, awaiting access</p>
      @if (me(); as identity) {
        <p class="detail" data-testid="guest-lobby-username">{{ identity.display_name }} ({{ identity.username }})</p>
      }
      <p class="detail">An admin has not granted you any permissions yet.</p>
      <fleet-kit-button testid="guest-lobby-logout" (click)="logout.emit()">Log out</fleet-kit-button>
    </div>
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
    }
    .lobby {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      padding: 24px 32px;
      border: 1px solid var(--line);
      background: var(--overlay-30);
      font-family: var(--mono);
    }
    .headline {
      font-size: var(--fs-lg);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--amber-hi);
      margin: 0;
    }
    .detail {
      color: var(--label);
      font-size: var(--fs-sm);
      margin: 0;
    }
  `,
})
export class GuestLobby {
  /** The resolved identity — always non-`null` while this renders (the app root only
   * shows the lobby once `/api/me` resolved authenticated-but-permissionless). */
  readonly me = input<MeResponse | null>(null);

  /** Fired when the operator clicks "Log out"; the container owns the mutation. */
  readonly logout = output<void>();
}
