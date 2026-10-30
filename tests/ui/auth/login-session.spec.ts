import { expect, test } from '../../harness/fixtures';

// exemplar: bzh:service-fixtures-only, bzh:service-clients-only, bzh:service-isolation-earned, bzh:service-waits-on-the-system.
// The browser's sign-in walk against the stub IdP, from the signed-out landing to a session ended mid-stream. The hub is
// the test's own, since the IdP's lever picks who the browser's dance resolves to for the whole hub.
test.use({ isolation: 'fresh-hub' });

const PROVIDER = 'stub';
const EMAIL = 'octocat@blizzard.test';
// An open stream the hub closes reconnects on the SPA's backoff ladder (1s, 2s, 4s, ... capped at 30s), and the first
// reconnect already carries the refusal; the bound clears the first two rungs rather than the whole ladder.
const RECONNECT_REFUSED_MS = 15_000;

test('test_browser_login_dance_and_mid_stream_session_expiry', async ({ page, api, idp, issue }) => {
  // A chunk with no promote rests not-ready: a concrete card for the read-only claim below.
  const { chunk } = await issue('login session');
  const chunkId = chunk.chunk_id;
  await idp.profile(EMAIL);

  // 1. The signed-out landing is the login page, one button, no automatic redirect.
  await page.goto('/');
  await expect(page.getByTestId('login-page')).toBeVisible();
  await expect(page.getByTestId(`login-provider-${PROVIDER}`)).toHaveCount(1);
  await expect(page.getByTestId('board-header')).toHaveCount(0);

  // The real dance against the stub IdP: the hub is invite-only, so a person it has never seen is handed back to the login
  // page with the refusal, holding no session.
  await page.getByTestId(`login-provider-${PROVIDER}`).click();
  await page.waitForURL('**/login?refusal=invite_only');
  await expect(page.getByTestId('login-page')).toBeVisible();
  await expect(page.getByTestId('login-refusal')).toHaveText(
    "You don't have access to anything here yet. Reach out to your administrator for access.",
  );
  await expect(page.getByTestId('board-header')).toHaveCount(0);
  expect((await page.context().cookies()).filter((cookie) => cookie.name === 'bz_session')).toEqual([]);

  // 2. Invited as guest, the board is reachable read-only. The invitation's link opens the board's landing page; its
  // provider button carries the token through the dance, and accepting it signs the person in.
  const invited = await api.post('/api/invitations', { data: { email: EMAIL, role: 'guest' } });
  expect(invited.status, JSON.stringify(invited.body)).toBe(201);
  const token = invited.body.token as string;
  await page.goto(`/invite/${token}`);
  await expect(page.getByTestId('invite-page')).toBeVisible();
  const inviteButton = page.getByTestId(`login-provider-${PROVIDER}`);
  await expect(inviteButton).toHaveAttribute('href', new RegExp(`&invite=${token}$`));
  await inviteButton.click();

  await expect(page.getByTestId('board-header')).toBeVisible();
  await expect(page.getByTestId('board-shell')).toBeVisible();
  await expect(page.getByTestId('no-access')).toHaveCount(0);
  expect((await page.context().cookies()).filter((cookie) => cookie.name === 'bz_session')).toHaveLength(1);
  // The chunk's card is visible, since a guest reads everything, but its Promote control is not.
  await expect(page.locator(`[data-chunk="${chunkId}"]`)).toBeVisible();
  await expect(page.getByTestId('promote-chunk')).toHaveCount(0);

  // 3. Promoted to contributor, the write control appears.
  const members = await api.get('/api/members');
  const member = (members.body as { user_id: string; email: string | null }[]).find((candidate) => candidate.email === EMAIL);
  expect(member, JSON.stringify(members.body)).toBeDefined();
  expect((await api.put(`/api/members/${member?.user_id}`, { data: { role: 'contributor' } })).ok).toBe(true);
  await page.reload();
  await expect(page.getByTestId('board-shell')).toBeVisible();
  await expect(page.getByTestId('promote-chunk')).toBeVisible();

  // 4. The session ends while this page's stream is open: a second page of the same browser logs out through the board's
  // own control, the hub closes the person's streams, and the open page's reconnect is refused and sent to login, with
  // no reload.
  const second = await page.context().newPage();
  await second.goto('/');
  await expect(second.getByTestId('board-shell')).toBeVisible();
  await second.getByTestId('app-nav-menu').click();
  await second.getByTestId('nav-logout').click();
  await expect(second.getByTestId('login-page')).toBeVisible();

  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: RECONNECT_REFUSED_MS });
  await expect(page.getByTestId(`login-provider-${PROVIDER}`)).toBeVisible();
});
