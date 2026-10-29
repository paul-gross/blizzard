import type { APIRequestContext, APIResponse } from '@playwright/test';
import { type Landing, walk } from './auth';
import type { HubCoordinates } from './hubs';
import type { Person } from './people';

const READY_DEADLINE_MS = 30_000;
const READY_POLL_MS = 200;
const RUNNER_SIGN_IN = '/api/auth/login?return_to=/';
const CHOOSER = '/login?return_to=';

/** The `{token, state}` form fields a hub delivers to a runner's callback. */
export interface Delivered {
  token: string;
  state: string;
  /** The `kid` in the delivered token's header: which signing key minted it. */
  keyId: string;
}

/** The authorize request a bounce left pending when the hub handed it to its `/login` chooser. */
export interface Pending {
  state: string | null;
  client: string | null;
}

/** What the hub answered an authorize request with: the delivered fields, or the redirect it sent instead. */
export interface Authorized {
  status: number;
  location: string | undefined;
  delivered: Delivered | undefined;
  /** Set when the answer is the chooser redirect. */
  pending: Pending | undefined;
}

/** What the runner's callback answered the form post with. */
export interface Completed {
  status: number;
  /** Whether the answer set the runner's session cookie. */
  session: boolean;
}

/** One sign-in bounce, from a browser at a runner: the runner side and the hub side each hold their own cookies. */
export interface Bounce {
  /** The runner's `state` for this bounce, read from the cookie it set. */
  state: string;
  /** The `client` the hub's authorize URL names: the runner's sign-in identity. */
  client: string | null;
  /** Sends the runner's authorize request to the hub, in `as`'s session or, with none, with no session at all. */
  authorize(as?: Person): Promise<Authorized>;
  /** Walks the pending authorize request through `provider` as `email`, and returns what the resumed request delivered. */
  resume(email: string, provider: string): Promise<Delivered>;
  /** Posts the delivered fields to the runner's callback, carrying the bounce cookies the runner set. */
  complete(delivered: Delivered): Promise<Completed>;
  /** The status the runner answers a `GET` of `path` with, from the browser at the runner, without following a redirect. */
  get(path: string): Promise<number>;
}

export interface Federation {
  /** Starts a bounce at the runner host serving `runnerUrl`; waits for the host to offer sign-in. */
  begin(runnerUrl: string): Promise<Bounce>;
}

export interface FederationContext {
  hub: Pick<HubCoordinates, 'url' | 'idpUrl' | 'auth'>;
  newContext: () => Promise<APIRequestContext>;
}

/** The `name=value` pairs a response's `Set-Cookie` headers set. */
function cookiesSet(response: APIResponse): Map<string, string> {
  const set = new Map<string, string>();
  for (const header of response.headersArray()) {
    if (header.name.toLowerCase() !== 'set-cookie') continue;
    const pair = header.value.split(';', 1)[0] ?? '';
    const cut = pair.indexOf('=');
    if (cut > 0) set.set(pair.slice(0, cut).trim(), pair.slice(cut + 1).trim());
  }
  return set;
}

function field(body: string, name: string): string | undefined {
  return new RegExp(`name="${name}" value="([^"]+)"`).exec(body)?.[1];
}

function deliveredIn(body: string): Delivered | undefined {
  const token = field(body, 'token');
  const state = field(body, 'state');
  if (token === undefined || state === undefined) return undefined;
  const header = JSON.parse(Buffer.from(token.split('.')[0] ?? '', 'base64url').toString('utf8')) as { kid?: string };
  if (typeof header.kid !== 'string') throw new Error('the delivered token carries no `kid` in its header');
  return { token, state, keyId: header.kid };
}

/**
 * The federation client. The runner side and the hub side of a bounce are separate request contexts: both daemons answer
 * on `127.0.0.1`, and cookies ignore the port, so one jar would send the hub's session to the runner. The runner's
 * cookies are read off its answers by prefix and sent back by name, as a browser at the runner would send them.
 */
export function federationClient(context: FederationContext): { federation: Federation; disposeAll: () => Promise<void> } {
  const made: APIRequestContext[] = [];
  const open = async (): Promise<APIRequestContext> => {
    const created = await context.newContext();
    made.push(created);
    return created;
  };
  const cookieHeader = (jar: Map<string, string>): Record<string, string> =>
    jar.size === 0 ? {} : { cookie: [...jar].map(([name, value]) => `${name}=${value}`).join('; ') };
  const hubUrl = (target: string): string => new URL(target, context.hub.url).href;

  const begin: Federation['begin'] = async (runnerUrl) => {
    const runner = await open();
    const browser = await open();
    const jar = new Map<string, string>();
    const send = (path: string): Promise<APIResponse> =>
      runner.fetch(`${runnerUrl}${path}`, { maxRedirects: 0, failOnStatusCode: false, headers: cookieHeader(jar), timeout: 30_000 });

    const deadline = Date.now() + READY_DEADLINE_MS;
    let started = await send(RUNNER_SIGN_IN);
    while (started.status() === 503) {
      if (Date.now() > deadline) throw new Error(`the runner at ${runnerUrl} still answered 503 to its sign-in route after ${READY_DEADLINE_MS} ms`);
      await new Promise((resolve) => setTimeout(resolve, READY_POLL_MS));
      started = await send(RUNNER_SIGN_IN);
    }
    const authorizeUrl = started.headers().location;
    if (![302, 307].includes(started.status()) || authorizeUrl === undefined) {
      throw new Error(`the runner's sign-in route answered ${started.status()} ${authorizeUrl ?? ''}, not a redirect to the hub: ${(await started.text()).slice(0, 300)}`);
    }
    const set = cookiesSet(started);
    for (const [name, value] of set) jar.set(name, value);
    const state = [...set].find(([name]) => name.startsWith('bz_runner_bounce_state_'))?.[1];
    if (state === undefined) throw new Error(`the runner's sign-in answer set no bounce-state cookie; it set ${[...set.keys()].join(', ') || 'none'}`);
    const client = new URL(authorizeUrl, context.hub.url).searchParams.get('client');
    let pendingPath: string | undefined;

    return {
      state,
      client,
      async authorize(as) {
        const answered = await (as?.context ?? browser).fetch(hubUrl(authorizeUrl), { maxRedirects: 0, failOnStatusCode: false, timeout: 30_000, maxRetries: 3 });
        const location = answered.headers().location;
        const body = answered.status() === 200 ? await answered.text() : '';
        let pending: Pending | undefined;
        if (location?.startsWith(CHOOSER)) {
          pendingPath = decodeURIComponent(location.slice(CHOOSER.length));
          const query = new URL(pendingPath, context.hub.url).searchParams;
          pending = { state: query.get('state'), client: query.get('client') };
        }
        return { status: answered.status(), location, delivered: deliveredIn(body), pending };
      },
      async resume(email, provider) {
        if (pendingPath === undefined) throw new Error('resume() needs a pending authorize request: call authorize() with no person first and let it reach the chooser');
        const startUrl = `${context.hub.url}/api/auth/${provider}/authorize?return_to=${encodeURIComponent(pendingPath)}`;
        const landed: Landing = await walk({ context: browser, hub: context.hub, email, startUrl });
        const delivered = deliveredIn(landed.body);
        if (landed.status !== 200 || delivered === undefined) {
          throw new Error(`resuming the pending authorize request through ${provider} landed at ${landed.status} ${landed.location ?? ''}: ${landed.body.slice(0, 300)}`);
        }
        return delivered;
      },
      async complete(delivered) {
        const answered = await runner.fetch(`${runnerUrl}/api/auth/callback`, {
          method: 'POST',
          maxRedirects: 0,
          failOnStatusCode: false,
          headers: { 'content-type': 'application/x-www-form-urlencoded', ...cookieHeader(jar) },
          data: new URLSearchParams({ token: delivered.token, state: delivered.state }).toString(),
          timeout: 30_000,
        });
        const sessionCookies = [...cookiesSet(answered)].filter(([name]) => name.startsWith('bz_runner_session_'));
        for (const [name, value] of sessionCookies) jar.set(name, value);
        return { status: answered.status(), session: sessionCookies.length > 0 };
      },
      async get(path) {
        return (await send(path)).status();
      },
    };
  };

  return {
    federation: { begin },
    disposeAll: async () => {
      await Promise.all(made.map((created) => created.dispose()));
    },
  };
}
