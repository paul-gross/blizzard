import type { APIRequestContext, TestInfo } from '@playwright/test';
import { recorderFor, timed } from './latency';
import type { Ledger } from './ledger';
import { RESOURCES } from './resources';

// The request path every client shares: the hub world's and the runner world's. It reaches neither world's fixtures.

// A hub answer is whatever the route returns; a test names the fields it reads.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface ApiResponse<T = Json> {
  status: number;
  ok: boolean;
  body: T;
  headers: Record<string, string>;
}

export interface CallOptions {
  params?: Record<string, string | number | boolean>;
  data?: unknown;
  headers?: Record<string, string>;
  /** Redirects followed before the answer comes back; 0 hands back the redirect itself. */
  maxRedirects?: number;
}

export type Verb = <T = Json>(route: string, options?: CallOptions) => Promise<ApiResponse<T>>;

export interface Verbs {
  get: Verb;
  post: Verb;
  put: Verb;
  patch: Verb;
  delete: Verb;
}

export type LogContext = () => string;

export interface Engine {
  context: APIRequestContext;
  testInfo: TestInfo;
  /** Headers every call carries: the tenant, or a runner's bearer token. */
  headers: Record<string, string>;
  ledger: Ledger;
  /** Log lines around a failure, attached with the response. */
  logContext: LogContext;
  /** What the log is of, in its attachment's name: the hub's unless a client of another process says otherwise. */
  logName?: string;
}

/** The route as recorded: no query, and no origin on a call that names its full URL. */
function recordedRoute(route: string): string {
  return route.replace(/^https?:\/\/[^/]+/, '').split('?')[0] as string;
}

export async function send(engine: Engine, method: string, route: string, options: CallOptions, narrow: boolean, expected?: number): Promise<ApiResponse> {
  const response = await timed(
    recorderFor(engine.testInfo),
    method,
    recordedRoute(route),
    () =>
      engine.context.fetch(route, {
        method,
        params: options.params,
        data: options.data,
        headers: { ...engine.headers, ...options.headers },
        maxRedirects: options.maxRedirects,
        failOnStatusCode: false,
        // A keep-alive socket the daemon closed while the test waited on another process resets; only a read is safe to resend.
        maxRetries: method === 'GET' ? 3 : 0,
      }),
    (answer) => answer.status(),
  );
  const text = await response.text();
  let body: Json = text;
  try {
    body = text === '' ? undefined : JSON.parse(text);
  } catch {
    // Not JSON: the text stands.
  }
  const call = `${method} ${route}`;
  if (response.status() >= 500 && response.status() !== expected) {
    await engine.testInfo.attach(`${call} → ${response.status()}`, { body: text, contentType: 'text/plain' });
    const log = engine.logContext();
    if (log !== '') await engine.testInfo.attach(`${engine.logName ?? 'hub'} log around ${call}`, { body: log, contentType: 'text/plain' });
    throw new Error(`${call} answered ${response.status()}, a server error: ${text.slice(0, 500)}`);
  }
  const routeKey = `${method} ${route.split('?')[0]}`;
  if (response.ok() && body !== null && typeof body === 'object') {
    for (const [kind, resource] of Object.entries(RESOURCES)) {
      if (resource.creates.includes(routeKey) && typeof body[resource.id] === 'string') {
        engine.ledger.add(kind, body[resource.id] as string);
      }
      const listField = resource.lists[routeKey];
      if (!narrow || listField === undefined) continue;
      const ours = (item: Json): boolean => engine.ledger.has(kind, item?.[resource.id]);
      if (listField === null) {
        if (Array.isArray(body)) body = body.filter(ours);
      } else if (Array.isArray(body[listField])) {
        body = { ...body, [listField]: (body[listField] as Json[]).filter(ours) };
      }
    }
  }
  return { status: response.status(), ok: response.ok(), body, headers: response.headers() };
}

export function verbs(engine: Engine, narrow: boolean, expected?: number): Verbs {
  const verb = (method: string): Verb => ((route, options = {}) => send(engine, method, route, options, narrow, expected)) as Verb;
  return { get: verb('GET'), post: verb('POST'), put: verb('PUT'), patch: verb('PATCH'), delete: verb('DELETE') };
}

/**
 * The hub client, acting as whoever the context's session is (the tenant's admin on a hub with authentication on, the
 * implicit operator on one with it off): every request carries `X-Blizzard-Tenant`. It records what the test
 * creates in the ledger, narrows the lists `resources.ts` names to it at the levels that share a tenant, and fails the
 * test on an unexpected `5xx`.

/** A fleet-plane client for one runner: its token names the tenant, so it sends no tenant header. */
export function fleetApi(options: { context: APIRequestContext; testInfo: TestInfo; token: string; ledger: Ledger; logContext: LogContext }): Verbs {
  return verbs(
    {
      context: options.context,
      testInfo: options.testInfo,
      headers: { Authorization: `Bearer ${options.token}` },
      ledger: options.ledger,
      logContext: options.logContext,
    },
    false,
  );
}


/** The verbs for one call whose `5xx` comes back to the test instead of failing it. */
export function expecting(engine: Engine, narrow: boolean, status: number): Verbs {
  return verbs(engine, narrow, status);
}
