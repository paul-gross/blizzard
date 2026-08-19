import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ProviderSummary } from '../api/hub';

/** The two provider marks the login page distinguishes — a GitHub-specific glyph for
 * `type = "github"`, and a generic SSO (key) glyph for every other configured type
 * (today, only `oidc`) rather than one icon per possible IdP brand. */
function markFor(type: string): 'github' | 'sso' {
  return type === 'github' ? 'github' : 'sso';
}

/**
 * The login page's provider buttons (issue #93) — presentational: one button per
 * `providers()` entry, each a real `<a>` to its hub authorize route (a full-page
 * navigation into the OAuth dance, not a router link) so the browser actually leaves
 * the SPA for the provider redirect. A single configured provider still renders as a
 * button — no auto-redirect (the AC's explicit "no surprise navigation on load").
 *
 * The last-used provider (by name, `lastUsed()`) is promoted to the top of the list
 * and marked, so a returning operator does not have to hunt for the button they used
 * last time.
 */
@Component({
  selector: 'fleet-login-buttons',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './login-buttons.html',
  styleUrl: './login-buttons.css',
})
export class LoginButtons {
  /** The configured providers, `GET /api/auth/providers`. */
  readonly providers = input<readonly ProviderSummary[]>([]);

  /** The last provider name used to sign in, or `null` if none remembered yet. */
  readonly lastUsed = input<string | null>(null);

  /** The same-origin path to return to once the dance completes — appended to each
   * authorize link as `return_to` (`hub/api/auth_login.py`'s `_safe_return_to`). */
  readonly returnTo = input<string>('/');

  /** Fired (with the provider's name) the instant a button is clicked, before the
   * browser follows the link's own navigation — the container persists it as the
   * new last-used provider. */
  readonly providerClick = output<string>();

  protected readonly markFor = markFor;

  /** {@link providers}, with the {@link lastUsed} provider (if present) promoted to
   * the front — every other provider keeps the server's own order. */
  protected readonly ordered = computed(() => {
    const list = this.providers();
    const last = this.lastUsed();
    if (last === null) return list;
    const idx = list.findIndex((p) => p.name === last);
    if (idx <= 0) return list;
    const promoted = list[idx];
    return [promoted, ...list.slice(0, idx), ...list.slice(idx + 1)];
  });

  protected hrefFor(name: string): string {
    return `/api/auth/${encodeURIComponent(name)}/authorize?return_to=${encodeURIComponent(this.returnTo())}`;
  }
}
