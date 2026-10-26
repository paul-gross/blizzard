import { expect, test } from '../../../harness/fixtures';

const stub = {
  name: 'stub', type: 'oidc', display_name: 'Stub', issuer: '${idp}',
  client_id: 'blizzard-tests', client_secret_env: 'BLIZZARD_TEST_IDP_SECRET', emails_authoritative: true,
};
const github = {
  name: 'github', type: 'github', display_name: 'GitHub', api_base: '${idp}',
  client_id: 'blizzard-tests', client_secret_env: 'BLIZZARD_TEST_IDP_SECRET',
};

test.describe('on the shared auth hub', () => {
  test.use({ isolation: 'common' });

  test('test_oidc_login_dance_against_the_stub_idp_ends_authenticated', async ({ user, api }) => {
    expect((await api.get('/api/auth/providers')).body).toEqual([{ name: 'stub', display_name: 'Stub', type: 'oidc' }]);
    const me = await user.api.get('/api/me');
    expect(me.status).toBe(200);
    expect(me.body.username).toBe(user.username);
    expect(me.body.user_id).toBe(user.userId);
    expect(me.body.tenants).toEqual(expect.arrayContaining([expect.objectContaining({ role: 'contributor' })]));
  });

  test('test_login_then_logout_round_trips_over_the_wire', async ({ user }) => {
    expect((await user.api.get('/api/me')).body.username).toBe(user.username);
    expect((await user.api.post('/api/auth/logout')).status).toBe(204);
    expect((await user.api.get('/api/me')).status).toBe(401);
  });

  test('test_an_uninvited_first_sign_in_lands_on_the_invite_only_refusal_with_no_session', async ({ people, anonymous }) => {
    const landed = await people.signIn('stranger@example.com');
    expect(landed.location).toBe('/login?refusal=invite_only');
    expect(landed.session).toBe(false);
    expect((await anonymous.get('/api/me')).status).toBe(401);
  });
});

test.describe('on a fresh auth hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test.describe('with GitHub', () => {
    test.use({ hubConfig: { auth: { oauth: { provider: [stub, github] } } } });

    test('test_github_login_dance_against_the_stub_idp_ends_authenticated', async ({ api, people }) => {
      const email = 'hub@example.com';
      const invited = await api.post('/api/invitations', { data: { email, role: 'contributor' } });
      expect(invited.status).toBe(201);
      const landed = await people.signIn(email, { invite: invited.body.token as string, provider: 'github' });
      expect(landed.session, JSON.stringify(landed)).toBe(true);
      const me = await landed.api?.get('/api/me');
      expect(me?.status).toBe(200);
      expect(me?.body.username).toBe('hub-example-com');
      expect(me?.body.tenants).toEqual(expect.arrayContaining([expect.objectContaining({ role: 'contributor' })]));
    });

    test('test_providers_endpoint_lists_both_configured_providers_over_the_wire', async ({ api, user }) => {
      expect((await api.get('/api/auth/providers')).body).toEqual([
        { name: 'stub', display_name: 'Stub', type: 'oidc' },
        { name: 'github', display_name: 'GitHub', type: 'github' },
      ]);
      expect((await user.api.get('/api/me')).body.username).toBe(user.username);
    });
  });

  test('test_refused_callback_lever_surfaces_as_a_login_failure', async ({ idp, people, anonymous }) => {
    expect((await idp.refuseCallback(true)).status).toBe(200);
    const landed = await people.signIn('refused@example.com');
    expect(landed.status).toBe(400);
    expect(JSON.parse(landed.body).error).toBe('login_failed');
    expect(landed.session).toBe(false);
    expect((await anonymous.get('/api/me')).status).toBe(401);
  });

  test.describe('authoritative bootstrap', () => {
    test.use({ hubConfig: { auth: { superuser: 'root@example.com' } } });

    test('test_named_superuser_email_lands_superuser_on_first_verified_login [authoritative]', async ({ people }) => {
      const landed = await people.signIn('root@example.com');
      expect(landed.session).toBe(true);
      const me = await landed.api?.get('/api/me');
      expect(me?.status).toBe(200);
      expect(me?.body.username).toBe('root-example-com');
      expect([me?.body.role, me?.body.hub_admin]).toEqual(['admin', true]);
    });
  });

  test.describe('non-authoritative bootstrap', () => {
    test.use({ hubConfig: { auth: { superuser: 'root@example.com', oauth: { provider: [{ ...stub, emails_authoritative: false }] } } } });

    test('test_named_superuser_email_lands_superuser_on_first_verified_login [not-authoritative]', async ({ people, anonymous }) => {
      const landed = await people.signIn('root@example.com');
      expect(landed.location).toBe('/login?refusal=invite_only');
      expect(landed.session).toBe(false);
      expect((await anonymous.get('/api/me')).status).toBe(401);
    });
  });
});
