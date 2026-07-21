import type { Router } from '@angular/router';

/** `sessionStorage` key the original route is stashed under before a 401 or an
 * auth-failed SSE stream routes to `/login` (issue #93) — read once by the login
 * page to build each provider link's `return_to`, so completing the dance lands back
 * where the app was interrupted rather than always on the board. `sessionStorage`
 * (not `localStorage`): the return location is this tab's navigation state, not a
 * durable preference — {@link LAST_PROVIDER_KEY} is the one thing meant to survive
 * across tabs/sessions. */
const RETURN_URL_KEY = 'fleet.auth.return-to';

/** Routes the app to `/login`, first stashing the current route (unless already on
 * `/login`, which would otherwise clobber a real return location with `/login`
 * itself) for {@link consumeReturnUrl} to read back once the dance completes. The one
 * seam both the 401 interceptor (`auth.interceptor.ts`) and the SSE auth-failure
 * channel (`../sse/fleet-live.ts`) route through, so "an unauthenticated response
 * means log in again" is decided in exactly one place. */
export function redirectToLogin(router: Router): void {
  const current = router.url;
  if (!current.startsWith('/login')) {
    sessionStorage.setItem(RETURN_URL_KEY, current);
  }
  void router.navigateByUrl('/login');
}

/** The stashed pre-login route, or `/` when none was recorded (a direct hit on
 * `/login`, or the very first unauthenticated load). Only a same-origin relative
 * path is ever honored server-side (`hub/api/auth_login.py`'s `_safe_return_to`); this
 * reads back exactly what {@link redirectToLogin} wrote, which is always
 * `router.url` — already such a path. */
export function consumeReturnUrl(): string {
  return sessionStorage.getItem(RETURN_URL_KEY) ?? '/';
}
