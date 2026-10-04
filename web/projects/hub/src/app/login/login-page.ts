import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { KitAsyncState } from 'fleet';
import { LoginButtons } from '../core/auth/login-buttons';
import { consumeReturnUrl, safeAuthorizeReturnTo } from '../core/auth/auth-redirect';
import { injectAuthProvidersQuery } from '../core/auth/providers.query';
import { providersState } from './login-page.model';

/** `localStorage` key the last provider signed in with is remembered under —
 * pinned by `login-page.spec.ts`'s "writes the last-used provider to
 * localStorage, not sessionStorage". */
const LAST_PROVIDER_KEY = 'fleet.auth.last-provider';

/**
 * The `/login` route — a container: owns the providers read and the
 * last-used-provider preference, forwards both to the presentational
 * {@link LoginButtons}. Reached either directly or via the 401 interceptor
 * (`auth.interceptor.ts`), which stashes the original route for
 * {@link consumeReturnUrl} to hand back to each provider link as `return_to` — so
 * completing the dance returns to where the app was interrupted.
 *
 * Under `auth.mode = "none"` the providers list is always empty (the hub's own
 * answer — never re-derived here), so this route renders no buttons; the app root
 * never routes here in that mode to begin with (`/api/me` never 401s under `none`).
 */
@Component({
  selector: 'app-login-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LoginButtons, KitAsyncState],
  templateUrl: './login-page.html',
  styleUrl: './login-page.css',
})
export class LoginPage {
  private readonly providersQuery = injectAuthProvidersQuery();
  private readonly route = inject(ActivatedRoute);

  /** Where completing a provider dance returns to — appended to every provider link,
   * read once (not reactively; it does not change while this page is mounted). A
   * `return_to` in the URL takes precedence: that is the hub-as-IdP multi-provider
   * bounce handing us a pending `/api/auth/authorize` request to resume,
   * honored only when {@link safeAuthorizeReturnTo} confirms it is exactly that. Absent
   * (the ordinary 401-interceptor path), it falls back to the route the interceptor
   * stashed via {@link consumeReturnUrl}. */
  protected readonly returnTo =
    safeAuthorizeReturnTo(this.route.snapshot.queryParamMap.get('return_to')) ?? consumeReturnUrl();

  protected readonly providers = computed(() => this.providersQuery.data() ?? []);

  protected readonly state = computed<'loading' | 'error' | 'empty' | 'ready'>(() =>
    providersState(this.providersQuery.isPending(), this.providersQuery.isError(), this.providers()),
  );

  private readonly lastUsedSignal = signal<string | null>(
    typeof localStorage === 'undefined' ? null : localStorage.getItem(LAST_PROVIDER_KEY),
  );
  protected readonly lastUsed = this.lastUsedSignal.asReadonly();

  protected rememberLastUsed(name: string): void {
    localStorage.setItem(LAST_PROVIDER_KEY, name);
    this.lastUsedSignal.set(name);
  }
}
