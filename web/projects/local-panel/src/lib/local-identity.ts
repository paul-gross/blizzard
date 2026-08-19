import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitButton } from 'fleet';

import { injectRunnerLogoutMutation, injectRunnerSessionQuery, signedInUsername } from './auth.query';

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
 * with the session now gone — deliberately: `session-recovery.ts`'s seam suspends
 * itself for the duration of this very logout (`runnerLogoutInFlight`), since the
 * session is clearing on purpose and this reload is the navigation for it. For every
 * *other* cause of session loss (issue #312, an expired session, a runner redeploy),
 * that same moment-after window is the seam's own to cover instead.
 *
 * Two shapes, one owner of the session read and the logout call. The default
 * `control` shape is the header's own username-plus-button block. The `label`
 * shape drops the button and marks the host `role="presentation"`, for the one
 * place the block sits inside a `role="menu"` panel (the runner's mobile
 * titlebar menu, issue #161/#163): a `role="menu"` may only own menu items, and
 * a plain `<button>` in there is unreachable — CDK's roving focus skips
 * non-`CdkMenuItem`s and `Tab` closes the menu rather than falling through to
 * it. The actionable half is a real `fleet-kit-menu-item` the panel's own
 * template declares, calling {@link logout} through a template reference —
 * `CdkMenu` finds its items by a content query that stops at a child
 * component's template boundary, so a menu item rendered in *here* would never
 * register with the panel out there.
 *
 * `role="presentation"` removes the host *element* from the accessibility tree
 * but not its text: a screen reader still reaches "signed in / alice" as content
 * of the menu. That is deliberate. The alternative, `aria-hidden="true"`, would
 * close the content model completely at the cost of hiding the signed-in
 * identity from exactly the users who cannot see it rendered — a worse trade for
 * a row that is genuinely informative. What mattered was removing the
 * *focusable* control, which is done.
 */
@Component({
  selector: 'local-identity',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  host: {
    '[attr.role]': "variant() === 'label' ? 'presentation' : null",
  },
  templateUrl: './local-identity.html',
  styleUrl: './local-identity.css',
})
export class LocalIdentity {
  protected readonly query = injectRunnerSessionQuery();
  private readonly logoutMutation = injectRunnerLogoutMutation();

  /** Which shape to render — `control` (the header block, with its own logout
   * button) or `label` (a non-focusable identity row for inside a menu panel,
   * whose logout is a menu item the panel declares; see the class docs). */
  readonly variant = input<'control' | 'label'>('control');

  /** The signed-in hub username to render the control for, or `null` — hiding it
   * entirely — under a `none`-mode hub or before any session resolves. Public so
   * a menu panel can gate its own `Log out` item on the same fact this block
   * gates itself on, rather than repeating the `auth_enabled`/`username` fold. */
  readonly username = computed<string | null>(() => signedInUsername(this.query.data()));

  /** Clears the session and reloads. Public so a `label`-shaped mount's sibling
   * menu item can invoke it through a template reference. */
  async logout(): Promise<void> {
    await this.logoutMutation.mutateAsync();
    this.reload();
  }

  /** Full page load so the served shell's SSO gate re-evaluates the next visit —
   * factored out so it can be stubbed in the component test. */
  protected reload(): void {
    globalThis.location.assign('/');
  }
}
