import { expect, test } from '../../../harness/fixtures';

const stub = {
  name: 'stub', type: 'oidc', display_name: 'Stub', issuer: '${idp}',
  client_id: 'blizzard-tests', client_secret_env: 'BLIZZARD_TEST_IDP_SECRET', emails_authoritative: true,
};
const second = { ...stub, name: 'oidc-b', display_name: 'Stub B' };

// Provider configuration and key rotation are hub-wide, and the runner declares its public origin before it first boots.
test.use({ isolation: 'fresh-hub', runnerStarted: false });

test.beforeEach(async ({ runnerHost }) => {
  runnerHost.reconfigure({ public_url: runnerHost.url });
  await runnerHost.restart();
});

test('test_the_wire_leg_ends_in_an_unlocked_runner_route', async ({ federation, runnerHost, user }) => {
  const bounce = await federation.begin(runnerHost.url);
  expect([302, 307]).toContain(await bounce.get('/'));

  const authorized = await bounce.authorize(user);
  expect(authorized.status).toBe(200);
  expect(authorized.delivered?.state).toBe(bounce.state);

  const completed = await bounce.complete(authorized.delivered!);
  expect(completed.status).toBe(303);
  expect(completed.session).toBe(true);
  expect(await bounce.get('/')).toBe(200);
});

test('test_key_rotation_is_picked_up_by_a_live_runner_with_no_restart', async ({ federation, runnerHost, user, api }) => {
  const pid = runnerHost.pid;

  const first = await federation.begin(runnerHost.url);
  const before = (await first.authorize(user)).delivered;
  expect((await first.complete(before!)).status).toBe(303);

  expect((await api.post('/api/auth/rotate-signing-key')).status).toBe(204);

  // A bounce minted under the rotated key: the live runner's key refetch on a miss must accept it.
  const second = await federation.begin(runnerHost.url);
  const after = (await second.authorize(user)).delivered;
  expect(after?.keyId).not.toBe(before?.keyId);
  const completed = await second.complete(after!);
  expect(completed.status).toBe(303);
  expect(completed.session).toBe(true);
  expect(runnerHost.pid).toBe(pid);
});

test.describe('with two providers', () => {
  test.use({ hubConfig: { auth: { oauth: { provider: [stub, second] } } } });

  test('test_a_two_provider_bounce_resumes_through_the_login_chooser', async ({ federation, runnerHost, people }) => {
    const joined = await people.create({ provider: 'oidc-b' });
    const bounce = await federation.begin(runnerHost.url);

    const chooser = await bounce.authorize();
    expect(chooser.status).toBeGreaterThanOrEqual(300);
    expect(chooser.location).toMatch(/^\/login\?return_to=/);
    expect(chooser.pending?.state).toBe(bounce.state);
    expect(chooser.pending?.client).toBeTruthy();

    const delivered = await bounce.resume(joined.email, 'oidc-b');
    expect(delivered.state).toBe(bounce.state);
    const completed = await bounce.complete(delivered);
    expect(completed.status).toBe(303);
    expect(completed.session).toBe(true);
  });
});
