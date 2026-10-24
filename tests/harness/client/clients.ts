import type { APIRequestContext, TestInfo } from '@playwright/test';
import { recorderFor, timed } from './latency';
import type { Ledger } from './ledger';
import type { MockIssue, MockRepositoryCreated } from './mock';
import { RESOURCES } from './resources';

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
}

type Verb = <T = Json>(route: string, options?: CallOptions) => Promise<ApiResponse<T>>;

export interface Verbs {
  get: Verb;
  post: Verb;
  put: Verb;
  patch: Verb;
  delete: Verb;
}

export interface HubApi extends Verbs {
  /** The same client with its lists not narrowed to what the test made. */
  readonly unfiltered: Verbs;
  /** The verbs for one call whose `5xx` comes back to the test instead of failing it. */
  expecting(status: number): Verbs;
}

/** What a client needs to read the hub's log around a server error. */
export type LogContext = () => string;

interface Engine {
  context: APIRequestContext;
  testInfo: TestInfo;
  /** Headers every call carries: the tenant, or a runner's bearer token. */
  headers: Record<string, string>;
  ledger: Ledger;
  /** Hub-side log lines around a failure, attached with the response. */
  logContext: LogContext;
}

/** The route as recorded: no query, and no origin on a call that names its full URL. */
function recordedRoute(route: string): string {
  return route.replace(/^https?:\/\/[^/]+/, '').split('?')[0] as string;
}

async function send(engine: Engine, method: string, route: string, options: CallOptions, narrow: boolean, expected?: number): Promise<ApiResponse> {
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
        failOnStatusCode: false,
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
    if (log !== '') await engine.testInfo.attach(`hub log around ${call}`, { body: log, contentType: 'text/plain' });
    throw new Error(`${call} answered ${response.status()}, a server error: ${text.slice(0, 500)}`);
  }
  const routeKey = `${method} ${route.split('?')[0]}`;
  if (response.ok() && body !== null && typeof body === 'object') {
    for (const [kind, resource] of Object.entries(RESOURCES)) {
      if (resource.creates.includes(routeKey) && typeof body[resource.id] === 'string') {
        engine.ledger.add(kind, body[resource.id] as string);
      }
      const listField = resource.lists[routeKey];
      if (narrow && listField !== undefined && Array.isArray(body[listField])) {
        body = {
          ...body,
          [listField]: (body[listField] as Json[]).filter((item) => engine.ledger.has(kind, item?.[resource.id])),
        };
      }
    }
  }
  return { status: response.status(), ok: response.ok(), body, headers: response.headers() };
}

function verbs(engine: Engine, narrow: boolean, expected?: number): Verbs {
  const verb = (method: string): Verb => ((route, options = {}) => send(engine, method, route, options, narrow, expected)) as Verb;
  return { get: verb('GET'), post: verb('POST'), put: verb('PUT'), patch: verb('PATCH'), delete: verb('DELETE') };
}

/**
 * The hub client, acting as the tenant's admin: every request carries `X-Blizzard-Tenant`. It records what the test
 * creates in the ledger, narrows the lists `resources.ts` names to it at the levels that share a tenant, and fails the
 * test on an unexpected `5xx`.
 */
export function hubApi(options: { context: APIRequestContext; testInfo: TestInfo; tenantId: string; narrow: boolean; ledger: Ledger; logContext: LogContext }): HubApi {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: { 'X-Blizzard-Tenant': options.tenantId },
    ledger: options.ledger,
    logContext: options.logContext,
  };
  return {
    ...verbs(engine, options.narrow),
    unfiltered: verbs(engine, false),
    expecting: (status) => verbs(engine, options.narrow, status),
  };
}

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

export interface ForgeApi {
  readonly url: string;
  createRepository(owner: string, name: string): Promise<MockRepositoryCreated>;
  createIssue(repository: string, issue: { title: string; body?: string; labels?: string[] }): Promise<MockIssue>;
  /** A raw call for the lever and control routes the forge's own README documents. */
  call(method: string, route: string, data?: unknown): Promise<ApiResponse>;
}

/** The hub's mock forge, reached at its own URL. Its repositories and issues are recorded as the test's own. */
export function forgeApi(options: { context: APIRequestContext; testInfo: TestInfo; url: string; ledger: Ledger }): ForgeApi {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: {},
    ledger: options.ledger,
    logContext: () => '',
  };
  const call = (method: string, route: string, data?: unknown): Promise<ApiResponse> =>
    send(engine, method, `${options.url}${route}`, { data }, false);
  const expectOk = (response: ApiResponse, what: string): Json => {
    if (!response.ok) throw new Error(`${what} answered ${response.status}: ${JSON.stringify(response.body)}`);
    return response.body;
  };
  return {
    url: options.url,
    call,
    async createRepository(owner, name) {
      const created = expectOk(await call('POST', '/_repos', { owner, name }), `creating ${owner}/${name}`) as MockRepositoryCreated;
      options.ledger.add('repository', created.full_name);
      return created;
    },
    async createIssue(repository, issue) {
      const created = expectOk(await call('POST', `/repos/${repository}/issues`, issue), `filing an issue in ${repository}`) as MockIssue;
      options.ledger.add('issue', `${repository}#${created.number}`);
      return created;
    },
  };
}
