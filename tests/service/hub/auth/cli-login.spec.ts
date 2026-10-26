import * as crypto from 'node:crypto';
import { expect, test } from '../../../harness/fixtures';

// The hub's half of `blizzard hub login`: the test plays the CLI against the hub's routes. Every read is about the
// person's own session or a token minted for it, so the tests share a tenant.
test.use({ isolation: 'common' });

const OOB_REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob';
const VERIFIER = 'a-fixed-service-test-verifier-with-plenty-of-entropy';
const CHALLENGE = crypto.createHash('sha256').update(VERIFIER).digest('base64url');
const STATE = 'svc-outer-state';
type HubApi = Parameters<Parameters<typeof test>[2]>[0]['user']['api'];

/** The authorize call a CLI sends the browser to, made in the person's own session with its redirect left unfollowed. */
const authorize = (user: { api: HubApi }, redirectUri: string) =>
  user.api.get('/api/auth/authorize', {
    params: { client: 'cli', redirect_uri: redirectUri, state: STATE, code_challenge: CHALLENGE, code_challenge_method: 'S256' },
    maxRedirects: 0,
  });

test('test_the_loopback_form_ends_in_a_working_bearer_session', async ({ user, bearer }) => {
  const redirectUri = 'http://127.0.0.1:54123/callback';
  const minted = await authorize(user, redirectUri);
  expect(minted.status).toBe(302);
  const location = new URL(minted.headers.location as string);
  expect(location.searchParams.get('state')).toBe(STATE);
  const code = location.searchParams.get('code') as string;

  const exchanged = await user.api.post('/api/auth/cli/token', { data: { code, code_verifier: VERIFIER, redirect_uri: redirectUri } });
  expect(exchanged.status).toBe(200);
  const token = exchanged.body.token as string;
  expect(token).toBeTruthy();

  const me = await (await bearer(token)).get('/api/me');
  expect(me.status).toBe(200);
  expect(me.body.username).toBe(user.username);
});

test('test_logout_revokes_the_bearer_session_server_side', async ({ user, bearer }) => {
  const redirectUri = 'http://127.0.0.1:54124/callback';
  const minted = await authorize(user, redirectUri);
  expect(minted.status).toBe(302);
  const code = new URL(minted.headers.location as string).searchParams.get('code') as string;
  const exchanged = await user.api.post('/api/auth/cli/token', { data: { code, code_verifier: VERIFIER, redirect_uri: redirectUri } });
  expect(exchanged.status).toBe(200);

  const cli = await bearer(exchanged.body.token as string);
  expect((await cli.get('/api/me')).status).toBe(200);
  expect((await cli.post('/api/auth/logout')).status).toBe(204);
  // The revoked session no longer resolves server-side, though the token is unchanged.
  expect((await cli.get('/api/me')).status).toBe(401);
});

test('test_the_paste_code_form_completes_the_same_login_with_no_loopback_listener', async ({ user, bearer }) => {
  // The out-of-band form renders a paste-able code in a page instead of redirecting.
  const minted = await authorize(user, OOB_REDIRECT_URI);
  expect(minted.status).toBe(200);
  const code = /<pre[^>]*>([^<]+)<\/pre>/.exec(String(minted.body))?.[1]?.trim();
  expect(code).toBeTruthy();

  const exchanged = await user.api.post('/api/auth/cli/token', { data: { code, code_verifier: VERIFIER, redirect_uri: OOB_REDIRECT_URI } });
  expect(exchanged.status).toBe(200);

  const me = await (await bearer(exchanged.body.token as string)).get('/api/me');
  expect(me.status).toBe(200);
  expect(me.body.username).toBe(user.username);
});
