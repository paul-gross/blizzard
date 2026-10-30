import { SESSION_COOKIE, sessionOf, usernameFor } from '../../auth';
import { expect, test } from '../../fixtures';

test.describe('idp.profile below fresh-hub', () => {
  test.use({ isolation: 'common' });

  test('is refused, naming fresh-hub', ({ idp }) => {
    expect(() => idp.profile('someone@blizzard.test')).toThrow(/idp\.profile\(\).*fresh-hub/);
  });
});

test.describe('idp.profile on a fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('makes a dance with no login hint resolve to the named identity', async ({ api, idp, testHub, playwright }) => {
    const email = 'browser@blizzard.test';
    const invited = await api.post('/api/invitations', { data: { email, role: 'guest' } });
    expect(invited.status).toBe(201);
    expect((await idp.profile(email)).status).toBe(200);

    // A browser's dance, followed by hand: the hub's authorize, the IdP's with no hint, the hub's callback.
    const context = await playwright.request.newContext();
    try {
      let url = `${testHub.coordinates.url}/api/auth/stub/authorize?invite=${encodeURIComponent(invited.body.token as string)}`;
      for (let hop = 0; hop < 12; hop++) {
        const response = await context.fetch(url, { maxRedirects: 0, failOnStatusCode: false });
        const location = response.headers().location;
        if (response.status() < 300 || response.status() >= 400 || location === undefined) break;
        url = new URL(location, url).href;
      }
      expect(await sessionOf(context), `no ${SESSION_COOKIE} after the dance`).toBeDefined();
      const me = await context.get(`${testHub.coordinates.url}/api/me`);
      expect((await me.json()).username).toBe(usernameFor(email));
    } finally {
      await context.dispose();
    }
  });
});
