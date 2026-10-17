import type { Router } from '@angular/router';

/** `sessionStorage` key the original route is stashed under before a 401 or an
 * auth-failed SSE stream routes to `/login`; read back by {@link consumeReturnUrl}. */
const RETURN_URL_KEY = 'fleet.auth.return-to';

/** Whether `url` is one of the pages a person reaches without a session — sign-in (and the refusals it shows) and an
 * invitation's landing. The app root renders them outside the shell whatever the session, and an unauthenticated
 * response never redirects away from one. */
export function isPublicUrl(url: string): boolean {
  const path = url.split(/[?#]/, 1)[0];
  return path === '/login' || path === '/invite' || path.startsWith('/invite/');
}

/** The route the router is on, or the one it is navigating to while a navigation runs — at boot, before the first
 * navigation ends, that is the page the tab opened on. */
function landingUrl(router: Router): string {
  const navigation = router.currentNavigation();
  return navigation === null ? router.url : router.serializeUrl(navigation.extractedUrl);
}

/** Routes the app to `/login`, first stashing the current route for {@link consumeReturnUrl} to read back once the
 * dance completes. On a public page ({@link isPublicUrl}) it does nothing: `/login` is already there and keeps its
 * query (a refusal, a resumed authorize request), and an invitation's landing is meant to be read signed out. The one
 * place the hub app decides "an unauthenticated hub response means log in again". */
export function redirectToLogin(router: Router): void {
  const current = landingUrl(router);
  if (isPublicUrl(current)) return;
  sessionStorage.setItem(RETURN_URL_KEY, current);
  void router.navigateByUrl('/login');
}

/** The stashed pre-login route, or `/` when none was recorded (a direct hit on
 * `/login`, or the very first unauthenticated load). */
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
