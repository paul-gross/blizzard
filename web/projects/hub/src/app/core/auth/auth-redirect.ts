import type { Router } from '@angular/router';

/** `sessionStorage` key the original route is stashed under before a 401 or an
 * auth-failed SSE stream routes to `/login`; read back by {@link consumeReturnUrl}.
 * `sessionStorage`
 * (not `localStorage`): the return location is this tab's navigation state, not a
 * durable preference — {@link LAST_PROVIDER_KEY} is the one thing meant to survive
 * across tabs/sessions. */
const RETURN_URL_KEY = 'fleet.auth.return-to';

/** Routes the app to `/login`, first stashing the current route (unless already on
 * `/login`, which would otherwise clobber a real return location with `/login`
 * itself) for {@link consumeReturnUrl} to read back once the dance completes. The one
 * place the hub app decides "an unauthenticated hub response means log in again". */
export function redirectToLogin(router: Router): void {
  const current = router.url;
  if (!current.startsWith('/login')) {
    sessionStorage.setItem(RETURN_URL_KEY, current);
  }
  void router.navigateByUrl('/login');
}

/** The stashed pre-login route, or `/` when none was recorded (a direct hit on
 * `/login`, or the very first unauthenticated load). Only a same-origin relative
 * path is ever honored server-side (the login route's `return_to` check); this
 * reads back exactly what {@link redirectToLogin} wrote, which is always
 * `router.url` — already such a path. */
export function consumeReturnUrl(): string {
  return sessionStorage.getItem(RETURN_URL_KEY) ?? '/';
}

/** Validates a URL-borne `return_to` (`/login?return_to=/api/auth/authorize?…`) for
 * the hub-as-IdP multi-provider bounce. Returns the value only
 * when it is a same-origin `/api/auth/authorize` request — never a cross-origin or
 * protocol-relative URL, and never any other path — so a crafted `/login?return_to=…`
 * link cannot turn the chooser into an open redirect or aim the resumed dance at a
 * non-authorize target. Anything else (including a normal SPA return route stashed for
 * the 401 flow) yields `null`, leaving {@link consumeReturnUrl} as the fallback. */
export function safeAuthorizeReturnTo(raw: string | null): string | null {
  if (raw === null) return null;
  const [path] = raw.split('?', 1);
  return path === '/api/auth/authorize' ? raw : null;
}
