import type { Router } from '@angular/router';

import { consumeReturnUrl, redirectToLogin, safeAuthorizeReturnTo } from './auth-redirect';

function fakeRouter(url: string): { router: Router; navigated: string[] } {
  const navigated: string[] = [];
  const router = {
    get url() {
      return url;
    },
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

  it('does not clobber a stashed return url when already on /login', () => {
    sessionStorage.setItem('fleet.auth.return-to', '/board');
    const { router, navigated } = fakeRouter('/login');
    redirectToLogin(router);

    expect(navigated).toEqual(['/login']);
    expect(consumeReturnUrl()).toBe('/board');
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
