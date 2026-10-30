import { expect, type RunnerHost, test } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned, bzh:service-public-surfaces.
// A browser's sign-in at runner hosts, through a real hub and the stub IdP. Runner hosts are laid out from the documented
// files by the harness. The IdP's profile is the hub's superuser, whose first sign-in needs no invitation and holds the
// admin membership a runner sign-in requires.
test.use({ isolation: 'fresh-hub', runnerStarted: false });

const SUPERUSER = 'superuser@blizzard.test';
// Two runners share this name: runner names are not unique, so only their hub-minted ids keep their cookies apart.
const SHARED_NAME = 'runner-e2e';
const TITLE = /blizzard runner/;
// An open stream a restarted runner drops reconnects on the SPA's backoff ladder (1s, 2s, 4s, ... capped at 30s), and
// which attempt carries the refusal is not fixed; the bound clears the ladder rather than racing it.
const RECONNECT_REFUSED_MS = 45_000;

const sessionCookie = (id: string): string => `bz_runner_session_${id}`;
const bounceStateCookie = (id: string): string => `bz_runner_bounce_state_${id}`;

/** A runner the operator minted at the hub, adopted by a host that is booted, whose adopted runner a pass registers. */
async function adopt(host: RunnerHost): Promise<void> {
  host.reconfigure({ public_url: host.url });
  await host.restart();
  // The pass answers once the hub has answered the runner's registration, which is what offers sign-in.
  await host.step();
}

/** The runner's bounce-state value, read off a login redirect's `Set-Cookie`: a `Secure` cookie is never attributed to a plain-http URL by the browser's jar. */
function bounceState(headers: { name: string; value: string }[], runnerId: string): string {
  for (const header of headers) {
    if (header.name.toLowerCase() !== 'set-cookie') continue;
    const match = new RegExp(`^${bounceStateCookie(runnerId)}=([^;]+)`).exec(header.value);
    if (match?.[1] !== undefined) return match[1];
  }
  throw new Error(`the login answer carried no ${bounceStateCookie(runnerId)} cookie: ${JSON.stringify(headers)}`);
}

test('test_multi_daemon_sso_bounce', async ({ page, api, fleet, idp, runnerHosts }) => {
  const minted = [await fleet(SHARED_NAME), await fleet(SHARED_NAME)] as const;
  const [a, b] = await Promise.all(
    minted.map((runner) => runnerHosts.create({ runnerAdopted: { name: SHARED_NAME, runnerId: runner.runnerId, token: runner.token } })),
  );
  for (const host of [a!, b!]) await adopt(host);
  const [idA, idB] = [minted[0].runnerId, minted[1].runnerId];
  await idp.profile(SUPERUSER);

  const requestUrls: string[] = [];
  const callbackBodies: string[] = [];
  page.on('request', (request) => {
    requestUrls.push(request.url());
    if (request.url() === `${a!.url}/api/auth/callback` && request.method() === 'POST') callbackBodies.push(request.postData() ?? '');
  });

  // 1. Runner A, no session: bounces through the hub and the stub IdP dance and lands back on runner A's own page, signed in.
  await page.goto(`${a!.url}/`);
  await expect(page).toHaveTitle(TITLE);
  expect((await page.context().cookies(a!.url)).map((cookie) => cookie.name)).toContain(sessionCookie(idA));
  // The hub-signed token never appears in a query string, across every request the browser made during the dance.
  expect(requestUrls.filter((url) => url.includes('token='))).toEqual([]);
  // The token the browser's own auto-submitted form post carried, which the rest of the test replays.
  expect(callbackBodies).not.toHaveLength(0);
  const captured = /token=([^&]+)/.exec(callbackBodies[0] ?? '')?.[1];
  expect(captured).toBeDefined();

  /** Posts `token` to the host's callback under a bounce state the host itself just minted, or under `state` when given. */
  const callback = async (host: RunnerHost, runnerId: string, state?: string): Promise<number> => {
    const login = await page.request.get(`${host.url}/api/auth/login?return_to=/`, { maxRedirects: 0 });
    expect([302, 307]).toContain(login.status());
    const own = bounceState(login.headersArray(), runnerId);
    const posted = await page.request.post(`${host.url}/api/auth/callback`, {
      headers: { 'content-type': 'application/x-www-form-urlencoded', cookie: `${bounceStateCookie(runnerId)}=${own}` },
      data: `token=${captured}&state=${state ?? own}`,
    });
    return posted.status();
  };

  // 2. Audience-binding: the same token presented to runner B, a different audience, is rejected even with a state B itself minted.
  expect(await callback(b!, idB)).toBe(400);
  // 3. Replay: the token presented to runner A again, with a fresh state of A's own so only the single-use check can fail it.
  expect(await callback(a!, idA)).toBe(400);
  // 4. A mismatched state is rejected outright.
  expect(await callback(a!, idA, 'not-the-real-state')).toBe(400);

  // 5. Key rotation mid-run, picked up with no restart: a bounce into runner B, whose key fetch must name the new key.
  const pids = [a!.pid, b!.pid];
  expect((await api.post('/api/auth/rotate-signing-key')).status).toBe(204);
  await page.goto(`${b!.url}/`);
  await expect(page).toHaveTitle(TITLE);
  const names = (await page.context().cookies()).map((cookie) => cookie.name);
  expect(names).toContain(sessionCookie(idB));
  // Same host, same name: B's sign-in left A's session cookie in the jar.
  expect(names).toContain(sessionCookie(idA));
  expect([a!.pid, b!.pid]).toEqual(pids);
});

test('test_runner_session_reacquisition_e2e', async ({ page, fleet, idp, runnerHosts }) => {
  test.setTimeout(120_000);
  const minted = await fleet(SHARED_NAME);
  const host = await runnerHosts.create({ runnerAdopted: { name: SHARED_NAME, runnerId: minted.runnerId, token: minted.token } });
  await adopt(host);
  await idp.profile(SUPERUSER);

  // 1. Authenticate into the panel.
  await page.goto(`${host.url}/`);
  await expect(page).toHaveTitle(TITLE);
  await expect(page.getByTestId('identity-username')).toBeVisible();
  const before = (await page.context().cookies(host.url)).find((cookie) => cookie.name === sessionCookie(minted.runnerId))?.value;
  expect(before).toBeDefined();

  // 2. Restart in place, same directory and port with no re-registration, as a redeploy would. The host mints its session
  // secret afresh per start, so the still-open tab's session is invalid while the hub's own is untouched.
  const bounced = page.waitForRequest((request) => request.url().includes('/api/auth/login?return_to='), { timeout: RECONNECT_REFUSED_MS });
  await host.restart();
  await host.step();

  // 3. No navigation and no reload: the panel's own reconnect to the restarted host is refused, and the session-recovery
  // seam silently bounces through the still-live hub session back with a fresh runner session.
  await bounced;
  await page.waitForLoadState('load');
  await expect(page.getByTestId('identity-username')).toBeVisible();
  const after = (await page.context().cookies(host.url)).find((cookie) => cookie.name === sessionCookie(minted.runnerId))?.value;
  expect(after, 'the session cookie never changed: no fresh bounce happened').not.toBe(before);
});

test.describe('a registered host of two runners', () => {
  test.use({ runnerConfig: { runner: [{ name: 'runner-local' }, { name: 'runner-two' }] } });

  test('test_one_sign_in_at_a_registered_host_reaches_both_runners_pages', async ({ page, api, idp, runnerHost }) => {
    runnerHost.reconfigure({ public_url: runnerHost.url });
    await runnerHost.restart();
    // The host signs in as itself only once the hub has answered its report.
    await runnerHost.stepHost();
    const hosts = await api.get('/api/hosts');
    expect(hosts.status).toBe(200);
    const [host] = hosts.body.hosts as { host_id: string }[];
    expect(hosts.body.hosts).toHaveLength(1);
    const hostId = host!.host_id;
    await idp.profile(SUPERUSER);

    const authorizes: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/auth/authorize')) authorizes.push(request.url());
    });
    const first = runnerHost.runners['runner-local']!.id;
    const second = runnerHost.runners['runner-two']!.id;

    // The legacy `/runners/<id>/board` link bounces to authorize with the host id as `client`.
    await page.goto(`${runnerHost.url}/runners/${first}/board`);
    await expect(page).toHaveTitle(TITLE);
    expect(authorizes.some((url) => url.includes(`client=${hostId}`)), JSON.stringify(authorizes)).toBe(true);
    // The hub's own session shares the loopback host; the panel's one session is named by the host id.
    const panelCookies = (await page.context().cookies(runnerHost.url)).map((cookie) => cookie.name).filter((name) => name.startsWith('bz_runner_session_'));
    expect(panelCookies).toEqual([sessionCookie(hostId)]);

    const bounces = authorizes.length;
    await page.goto(`${runnerHost.url}/runners/${second}/board`);
    await expect(page).toHaveTitle(TITLE);
    await expect(page.getByTestId('identity-username')).toBeVisible();
    expect(authorizes, "the second runner's page bounced to the hub again").toHaveLength(bounces);
  });
});
