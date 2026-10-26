import type { Role } from '../../client/wire/hub/types.gen';
import { expect, test } from '../../fixtures';

const ROLES: Role[] = ['guest', 'contributor', 'admin'];

test.describe('people at the common tenant', () => {
  test.use({ isolation: 'common' });

  test('six people made at once each answer GET /api/me as themselves, in the role they were invited as', async ({ people }) => {
    const made = await Promise.all(Array.from({ length: 6 }, (_, index) => people.create({ role: ROLES[index % ROLES.length] })));
    expect(new Set(made.map((person) => person.userId)).size).toBe(6);
    for (const person of made) {
      const me = await person.api.get('/api/me');
      expect(me.status).toBe(200);
      expect(me.body.username).toBe(person.username);
      expect(me.body.user_id).toBe(person.userId);
      expect(me.body.role).toBe(person.role);
    }
  });
});

test.describe('the superuser', () => {
  test.describe('at the common tenant', () => {
    test.use({ isolation: 'common' });

    test('api acts as a hub admin', async ({ api }) => {
      const me = await api.get('/api/me');
      expect(me.status).toBe(200);
      expect(me.body.hub_admin).toBe(true);
    });
  });

  test.describe('at fresh-tenant', () => {
    test.use({ isolation: 'fresh-tenant' });

    test('is the admin of the tenant the test got', async ({ api, testHub }) => {
      const superuser = await testHub.superuser();
      const members = await api.get('/api/members');
      expect(members.status).toBe(200);
      expect(members.body.filter((member: { user_id: string }) => member.user_id === superuser?.userId).map((member: { role: string }) => member.role)).toEqual(['admin']);
    });
  });
});

test.describe('the bootstrap on a fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('people.signIn of the configured superuser comes back a hub admin, with nobody signed in before', async ({ people, testHub }) => {
    const signed = await people.signIn('superuser@blizzard.test');
    expect(signed.session).toBe(true);
    const me = await signed.api!.get('/api/me');
    expect(me.body.hub_admin).toBe(true);
    // The test named neither the tenant nor the superuser's credential, so nothing signed the superuser in on its behalf.
    expect(testHub.coordinates.auth?.session).toBeUndefined();
  });

  test('an uninvited sign-in is refused with no session', async ({ people }) => {
    const signed = await people.signIn('stranger@blizzard.test');
    expect(signed.session).toBe(false);
    expect(signed.location).toContain('/login?refusal=');
    expect(signed.context).toBeUndefined();
  });

  test('a provider the hub does not declare is refused before any request', async ({ people }) => {
    await expect(people.signIn('someone@blizzard.test', { provider: 'nope' })).rejects.toThrow(/no provider "nope"/);
  });

  test('user and api work on the fresh hub once the superuser has signed in', async ({ user, api }) => {
    expect((await user.api.get('/api/me')).body.role).toBe('contributor');
    expect((await api.get('/api/me')).body.hub_admin).toBe(true);
  });
});

test.describe('user as a guest', () => {
  test.use({ isolation: 'common', userRole: 'guest' });

  test('user.api answers GET /api/me as a guest', async ({ user }) => {
    const me = await user.api.get('/api/me');
    expect(me.status).toBe(200);
    expect(me.body.role).toBe('guest');
    expect(me.body.username).toBe(user.username);
  });

  test('members and invitations come back narrowed to the test’s own', async ({ api, user }) => {
    const members = await api.get('/api/members');
    expect(members.body.map((member: { user_id: string }) => member.user_id)).toEqual([user.userId]);
    const invitations = await api.get('/api/invitations');
    expect(invitations.body.invitations.map((invitation: { email: string }) => invitation.email)).toEqual([user.email]);
    const whole = await api.unfiltered.get('/api/members');
    expect(whole.body.length).toBeGreaterThan(1);
  });
});
