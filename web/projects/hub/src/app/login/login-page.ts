import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { KitAsyncState, LoginButtons, consumeReturnUrl, injectAuthProvidersQuery } from 'fleet';

/** `localStorage` key the last provider signed in with is remembered under (issue
 * #93) — `localStorage`, not `sessionStorage`: a returning operator's preference
 * should survive across tabs and browser restarts, unlike the one-shot return
 * location {@link consumeReturnUrl} reads. */
const LAST_PROVIDER_KEY = 'fleet.auth.last-provider';

/**
 * The `/login` route (issue #93) — a container: owns the providers read and the
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
  template: `
    <div class="login" data-testid="login-page">
      <h1 class="title">blizzard</h1>
      <p class="subtitle">Sign in to continue</p>
      <div class="body">
        <fleet-kit-async-state
          [state]="state()"
          loadingText="LOADING PROVIDERS…"
          errorText="LOGIN UNAVAILABLE"
          emptyText="No login providers configured."
          emptyTestid="login-no-providers"
        >
          <fleet-login-buttons
            [providers]="providers()"
            [lastUsed]="lastUsed()"
            [returnTo]="returnTo"
            (providerClick)="rememberLastUsed($event)"
          />
        </fleet-kit-async-state>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      font-family: var(--mono);
    }
    .login {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      width: 320px;
    }
    .title {
      margin: 0;
      color: var(--amber-hi);
      font-size: var(--fs-xl, 28px);
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .subtitle {
      margin: 0 0 20px;
      color: var(--label);
      font-size: var(--fs-sm);
      letter-spacing: 0.1em;
    }
    .body {
      position: relative;
      width: 100%;
      min-height: 60px;
    }
  `,
})
export class LoginPage {
  private readonly providersQuery = injectAuthProvidersQuery();

  /** The route stashed by the 401 interceptor before it landed here — appended to
   * every provider link so completing the dance returns where the app was
   * interrupted, read once (not reactively — it does not change while this page
   * is mounted). */
  protected readonly returnTo = consumeReturnUrl();

  protected readonly providers = computed(() => this.providersQuery.data() ?? []);

  protected readonly state = computed<'loading' | 'error' | 'empty' | 'ready'>(() => {
    if (this.providersQuery.isPending()) return 'loading';
    if (this.providersQuery.isError()) return 'error';
    return this.providers().length === 0 ? 'empty' : 'ready';
  });

  private readonly lastUsedSignal = signal<string | null>(
    typeof localStorage === 'undefined' ? null : localStorage.getItem(LAST_PROVIDER_KEY),
  );
  protected readonly lastUsed = this.lastUsedSignal.asReadonly();

  protected rememberLastUsed(name: string): void {
    localStorage.setItem(LAST_PROVIDER_KEY, name);
    this.lastUsedSignal.set(name);
  }
}
