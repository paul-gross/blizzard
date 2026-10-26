import * as crypto from 'node:crypto';
import type { APIRequestContext } from '@playwright/test';
import type { HubCoordinates } from './hubs';

export const SESSION_COOKIE = 'bz_session';
/** The provider a walk signs in through unless the call names another the hub's configuration declares. */
export const DEFAULT_PROVIDER = 'stub';

const MAX_HOPS = 12;

/** Where a walk stopped: the first answer that is not a redirect on to the hub's `/api/auth/` or to the IdP. */
export interface Landing {
  status: number;
  location: string | undefined;
  body: string;
  /** Whether any hop of the walk set a `bz_session`. */
  session: boolean;
}

/**
 * The identity the IdP holds for an email. The subject is derived from the lower-cased email: two emails never share
 * one, which the IdP's default subject would do, and one email always resolves to the same person.
 */
export function identityFor(email: string): { subject: string; handle: string; email: string; email_verified: boolean } {
  const lower = email.toLowerCase();
  return { subject: crypto.createHash('sha256').update(lower).digest('hex').slice(0, 24), handle: lower, email: lower, email_verified: true };
}

/**
 * The username the hub mints for an email's identity: the handle, which is the email, slugified. `GET /api/me` carries no
 * email, so this is how a check tells whose answer it holds.
 */
export function usernameFor(email: string): string {
  return email.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '');
}

/** Registers the email's identity with the stack's IdP. */
export async function registerIdentity(idpUrl: string, email: string): Promise<void> {
  const response = await fetch(`${idpUrl}/_levers/identities`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(identityFor(email)),
    signal: AbortSignal.timeout(30_000),
  });
  if (response.status !== 200) throw new Error(`registering ${email} with the IdP answered ${response.status}: ${await response.text()}`);
}

/** The providers a sign-in may name: refused before any request when the hub's configuration does not declare it. */
export function requireProvider(hub: Pick<HubCoordinates, 'auth'>, provider: string): void {
  if (hub.auth === undefined) throw new Error('the hub runs with authentication off; sign-in needs the `auth` profile');
  if (!hub.auth.providers.includes(provider)) {
    throw new Error(`the hub declares no provider "${provider}" (it declares ${hub.auth.providers.map((name) => `"${name}"`).join(', ') || 'none'})`);
  }
}

/**
 * The one sign-in walk. It registers the identity with the IdP, then starts at `startUrl`, by default the provider's
 * `authorize` (with `invite` when there is one), and follows redirects by hand: the IdP's URL gets `login_hint=<email>`,
 * the hub's own `/api/auth/` redirects are followed as they come. The context keeps whatever cookie the hub sets.
 */
export async function walk(options: {
  context: APIRequestContext;
  hub: Pick<HubCoordinates, 'url' | 'idpUrl' | 'auth'>;
  email: string;
  invite?: string;
  provider?: string;
  startUrl?: string;
}): Promise<Landing> {
  const provider = options.provider ?? DEFAULT_PROVIDER;
  if (options.startUrl === undefined) requireProvider(options.hub, provider);
  await registerIdentity(options.hub.idpUrl, options.email);
  const hubOrigin = new URL(options.hub.url).origin;
  const idpOrigin = new URL(options.hub.idpUrl).origin;
  let url =
    options.startUrl ??
    `${options.hub.url}/api/auth/${provider}/authorize${options.invite === undefined ? '' : `?invite=${encodeURIComponent(options.invite)}`}`;
  let session = false;
  for (let hop = 0; hop < MAX_HOPS; hop++) {
    const response = await options.context.fetch(url, { maxRedirects: 0, failOnStatusCode: false, timeout: 30_000 });
    session ||= response.headersArray().some((header) => header.name.toLowerCase() === 'set-cookie' && header.value.startsWith(`${SESSION_COOKIE}=`));
    const status = response.status();
    const location = response.headers().location;
    if (status >= 300 && status < 400 && location !== undefined) {
      const next = new URL(location, url);
      if (next.origin === idpOrigin) {
        next.searchParams.set('login_hint', options.email.toLowerCase());
        url = next.href;
        continue;
      }
      if (next.origin === hubOrigin && next.pathname.startsWith('/api/auth/')) {
        url = next.href;
        continue;
      }
    }
    return { status, location, body: await response.text(), session };
  }
  throw new Error(`the sign-in walk for ${options.email} followed ${MAX_HOPS} redirects without landing; it was at ${url}`);
}

/** The `bz_session` value a context holds, or `undefined` when it holds none. */
export async function sessionOf(context: APIRequestContext): Promise<string | undefined> {
  const state = await context.storageState();
  return state.cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.value;
}

/** The superuser's credential: the session value `HubInternal` and a request context send, and the user id a tenant names as its admin. */
export interface Superuser {
  session: string;
  userId: string;
}

/**
 * Signs the hub's `auth.superuser` in through `context`, which keeps the session, and reads the user id from
 * `GET /api/me`. The first sign-in on a hub claims the bootstrap.
 */
export async function signInSuperuser(hub: Pick<HubCoordinates, 'url' | 'idpUrl' | 'auth'>, context: APIRequestContext): Promise<Superuser> {
  const email = hub.auth?.superuser;
  if (email === undefined) throw new Error('the hub names no `auth.superuser`, so the harness has no superuser to sign in');
  const landing = await walk({ context, hub, email });
  const session = await sessionOf(context);
  if (!landing.session || session === undefined) {
    throw new Error(`signing the superuser ${email} in set no session; the walk landed at ${landing.status} ${landing.location ?? ''}: ${landing.body.slice(0, 300)}`);
  }
  const me = await context.get(`${hub.url}/api/me`, { failOnStatusCode: false });
  const body = (await me.json().catch(() => ({}))) as { user_id?: string; hub_admin?: boolean };
  if (me.status() !== 200 || typeof body.user_id !== 'string' || body.hub_admin !== true) {
    throw new Error(`the superuser ${email} signed in but GET /api/me answered ${me.status()} ${JSON.stringify(body)}, not a hub admin`);
  }
  return { session, userId: body.user_id };
}
