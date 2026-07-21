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
  template: `
    <ul class="providers" data-testid="login-providers">
      @for (provider of ordered(); track provider.name) {
        <li>
          <a
            class="provider-btn"
            [href]="hrefFor(provider.name)"
            [attr.data-testid]="'login-provider-' + provider.name"
            [attr.data-provider-type]="provider.type"
            (click)="providerClick.emit(provider.name)"
          >
            <span class="mark" [attr.data-testid]="'login-provider-mark-' + markFor(provider.type)">
              @if (markFor(provider.type) === 'github') {
                <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                       0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                       -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                       .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                       -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09
                       2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15
                       0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0
                       .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"
                  />
                </svg>
              } @else {
                <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M10 1a4 4 0 0 0-3.86 5.03L1 11.17V14a1 1 0 0 0 1 1h2.83a1 1 0 0 0
                       .7-.29l.97-.97a1 1 0 0 0 .29-.7V12h1a1 1 0 0 0 1-1v-1h1a1 1 0 0
                       0 .71-.29l.4-.4A4 4 0 1 0 10 1Zm1.5 4.5a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z"
                  />
                </svg>
              }
            </span>
            <span class="label">{{ provider.display_name }}</span>
            @if (provider.name === lastUsed()) {
              <span class="last-used" data-testid="login-provider-last-used">last used</span>
            }
          </a>
        </li>
      }
    </ul>
  `,
  styles: `
    :host {
      display: block;
    }
    .providers {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .provider-btn {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border: 1px solid var(--line);
      background: var(--overlay-30);
      color: var(--text);
      text-decoration: none;
      font-family: var(--mono);
      font-size: var(--fs-base);
      cursor: pointer;
    }
    .provider-btn:hover {
      border-color: var(--cyan);
    }
    .mark {
      display: inline-flex;
      align-items: center;
    }
    .label {
      flex: 1;
    }
    .last-used {
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--cyan);
    }
  `,
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
