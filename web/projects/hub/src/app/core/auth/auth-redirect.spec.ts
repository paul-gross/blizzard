import type { Router } from '@angular/router';

import { consumeReturnUrl, isPublicUrl, redirectToLogin, safeAuthorizeReturnTo } from './auth-redirect';

/** A router on `url`, or navigating to `inFlight` when one is given. */
function fakeRouter(url: string, inFlight?: string): { router: Router; navigated: string[] } {
  const navigated: string[] = [];
  const router = {
    get url() {
      return url;
    },
    currentNavigation: () => (inFlight === undefined ? null : { extractedUrl: inFlight }),
    serializeUrl: (tree: unknown) => String(tree),
    navigateByUrl: (target: string) => {
      navigated.push(target);
      return Promise.resolve(true);
    },
  } as unknown as Router;
  return { router, navigated };
}

describe('redirectToLogin / consumeReturnUrl', () => {
  afterEach(() => sessionStorage.clear());

  it('stashes the current route and navigates to /login', () => {
    const { router, navigated } = fakeRouter('/graphs/gr_1');
    redirectToLogin(router);

    expect(navigated).toEqual(['/login']);
    expect(consumeReturnUrl()).toBe('/graphs/gr_1');
  });

  it('stays on /login, keeping its query and the stashed return url', () => {
    sessionStorage.setItem('fleet.auth.return-to', '/board');
    const { router, navigated } = fakeRouter('/login?refusal=invite_only');
    redirectToLogin(router);

    expect(navigated).toEqual([]);
    expect(consumeReturnUrl()).toBe('/board');
  });

  it('leaves an invitation landing alone, so it is read signed out', () => {
    const { router, navigated } = fakeRouter('/invite/tok_1');
    redirectToLogin(router);

    expect(navigated).toEqual([]);
    expect(sessionStorage.getItem('fleet.auth.return-to')).toBeNull();
  });

  it('judges the page a navigation is heading to, as at boot before the first navigation ends', () => {
    const toInvite = fakeRouter('/', '/invite/tok_1');
    redirectToLogin(toInvite.router);
    expect(toInvite.navigated).toEqual([]);

    const toGraphs = fakeRouter('/', '/graphs/gr_1');
    redirectToLogin(toGraphs.router);
    expect(toGraphs.navigated).toEqual(['/login']);
    expect(consumeReturnUrl()).toBe('/graphs/gr_1');
  });

  it('counts sign-in and invitation pages as public, and nothing else', () => {
    expect(isPublicUrl('/login')).toBe(true);
    expect(isPublicUrl('/login?refusal=invitation_unusable')).toBe(true);
    expect(isPublicUrl('/invite/tok_1')).toBe(true);
    expect(isPublicUrl('/invite/accepted?tenant=ten_1')).toBe(true);
    expect(isPublicUrl('/')).toBe(false);
    expect(isPublicUrl('/loginx')).toBe(false);
    expect(isPublicUrl('/invites')).toBe(false);
    expect(isPublicUrl('/board?next=/login')).toBe(false);
  });

  it('falls back to / when nothing was ever stashed', () => {
    expect(consumeReturnUrl()).toBe('/');
  });
});

describe('safeAuthorizeReturnTo', () => {
  it('honors a same-origin /api/auth/authorize request, query string intact', () => {
    const raw = '/api/auth/authorize?client=runner-a&redirect_uri=https://runner-a.example/cb&state=s';
    expect(safeAuthorizeReturnTo(raw)).toBe(raw);
  });

  it('honors the bare /api/auth/authorize path', () => {
    expect(safeAuthorizeReturnTo('/api/auth/authorize')).toBe('/api/auth/authorize');
  });

  it('rejects an absolute cross-origin URL (open-redirect guard)', () => {
    expect(safeAuthorizeReturnTo('https://evil.example/api/auth/authorize')).toBeNull();
  });

  it('rejects a protocol-relative //host target', () => {
    expect(safeAuthorizeReturnTo('//evil.example/api/auth/authorize')).toBeNull();
  });

  it('rejects any other same-origin path', () => {
    expect(safeAuthorizeReturnTo('/graphs/gr_1')).toBeNull();
    expect(safeAuthorizeReturnTo('/api/auth/authorizer/evil')).toBeNull();
  });

  it('returns null for a null input', () => {
    expect(safeAuthorizeReturnTo(null)).toBeNull();
  });
});
