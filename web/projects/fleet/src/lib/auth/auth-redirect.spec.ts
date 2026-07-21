import type { Router } from '@angular/router';

import { consumeReturnUrl, redirectToLogin } from './auth-redirect';

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
