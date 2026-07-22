import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { KitButton } from 'fleet';

import { injectRunnerLogoutMutation, injectRunnerSessionQuery } from './auth.query';

/**
 * The panel header's identity control (issue #129) — the signed-in hub username
 * beside a logout button, off `GET /api/auth/session`. Rendered **only** under an
 * oauth-mode hub with a resolved session (`auth_enabled` and a `username`); under a
 * `none`-mode hub the surface is authless, so the query answers `auth_enabled: false`
 * and this renders nothing at all — no username, no logout.
 *
 * Logout clears the runner's own session cookie (`POST /api/auth/logout`), then reloads
 * so the served shell's SSO gate re-evaluates the next visit: a still-live hub session
 * silently re-authenticates through the bounce (correct — ending fleet-wide access is
 * *hub* logout), an ended one lands on the hub's login surface. The reload also escapes
 * the moment-after state where every other rail would start `401`ing on its next poll
 * with the session now gone.
 */
@Component({
  selector: 'local-identity',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    @if (username(); as user) {
      <div class="identity" data-testid="local-identity">
        <span class="who">
          <span class="lbl">signed in</span>
          <span class="user" data-testid="identity-username">{{ user }}</span>
        </span>
        <fleet-kit-button class="logout" testid="identity-logout" (click)="logout()">Log out</fleet-kit-button>
      </div>
    }
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
    }
    .identity {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 12px;
      border-left: 1px solid var(--line);
      white-space: nowrap;
    }
    .who {
      display: flex;
      flex-direction: column;
      justify-content: center;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .user {
      color: var(--cyan);
      font-size: var(--fs-sm);
    }
    .logout {
      align-items: center;
    }
  `,
})
export class LocalIdentity {
  protected readonly query = injectRunnerSessionQuery();
  private readonly logoutMutation = injectRunnerLogoutMutation();

  /** The signed-in hub username to render the control for, or `null` — hiding it
   * entirely — under a `none`-mode hub or before any session resolves. */
  protected readonly username = computed<string | null>(() => {
    const session = this.query.data();
    return session?.auth_enabled ? (session.username ?? null) : null;
  });

  protected async logout(): Promise<void> {
    await this.logoutMutation.mutateAsync();
    this.reload();
  }

  /** Full page load so the served shell's SSO gate re-evaluates the next visit —
   * factored out so it can be stubbed in the component test. */
  protected reload(): void {
    globalThis.location.assign('/');
  }
}
