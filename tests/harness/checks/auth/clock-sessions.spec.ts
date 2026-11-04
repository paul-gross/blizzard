import { expect, test } from '../../fixtures';

const PAST_IDLE_TTL_SECONDS = 25 * 60 * 60;

test.describe('sessions on a controlled stack with authentication on', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('after an advance past IDLE_TTL, the superuser’s api and a person’s client each answer 200', async ({ api, people, clock }) => {
    const person = await people.create({ role: 'contributor' });
    expect((await api.get('/api/me')).status).toBe(200);
    await clock.advance(PAST_IDLE_TTL_SECONDS);
    const superuser = await api.get('/api/me');
    expect(superuser.status).toBe(200);
    expect(superuser.body.hub_admin).toBe(true);
    const own = await person.api.get('/api/me');
    expect(own.status).toBe(200);
    expect(own.body.user_id).toBe(person.userId);
  });

  test('after suspendSessionRefresh, the person’s next request answers 401, and the one after is refreshed', async ({ people, clock }) => {
    const person = await people.create({ role: 'contributor' });
    await clock.advance(PAST_IDLE_TTL_SECONDS);
    clock.suspendSessionRefresh();
    expect((await person.api.get('/api/me')).status).toBe(401);
    expect((await person.api.get('/api/me')).status).toBe(200);
  });

  test('a later advance never fails with a 401 on the stack’s own hub member', async ({ clock }) => {
    await clock.advance(PAST_IDLE_TTL_SECONDS);
    clock.suspendSessionRefresh();
    await clock.advance(PAST_IDLE_TTL_SECONDS);
    expect(clock.now().getTime()).toBeGreaterThan(Date.now() + 2 * PAST_IDLE_TTL_SECONDS * 1000 - 60_000);
  });
});
