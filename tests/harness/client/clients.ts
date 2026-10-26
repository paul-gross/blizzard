import type { APIRequestContext, TestInfo } from '@playwright/test';
import { recorderFor, timed } from './latency';
import type { Ledger } from './ledger';
import type { MockDriveAnswer, MockIssue, MockRepositoryCreated, MockRunnerLeverListing } from './mock';
import { RESOURCES } from './resources';
import type { SweepPassResponse } from './wire/hub/types.gen';
import { requireFreshHub } from '../factories';
import type { Level } from '../tenancy';

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

/** The sweeps a pass over one tenant serves; the other three run over every tenant only. */
export const TENANT_SWEEPS = ['forge_status', 'event_derivation', 'close_drain'] as const;
export type TenantSweep = (typeof TENANT_SWEEPS)[number];

/** Every sweep the hub builds, by its name in `[sweeps.<name>]`. */
export const ALL_SWEEPS = [...TENANT_SWEEPS, 'tenant_teardown', 'trace_export', 'egress'] as const;
export type SweepName = (typeof ALL_SWEEPS)[number];

export interface HubApi extends Verbs {
  /**
   * Runs one pass of a sweep over the test's tenant and returns once it has finished, so a test acts on the sweep's
   * outcome rather than a timer. Every answer but a `completed` pass fails the test, naming the sweep, the status, and
   * the answer's `error`; a pass that raced another is named as such.
   */
  sweep(name: TenantSweep): Promise<SweepPassResponse>;
  /**
   * The same pass over every tenant of the hub, any of the six sweeps. Refused before any request is sent below
   * `fresh-hub`, where another test's tenant would be swept too.
   */
  sweep(name: SweepName, options: { allTenants: true }): Promise<SweepPassResponse>;
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

function verbs(engine: Engine, narrow: boolean, expected?: number): Verbs {
  const verb = (method: string): Verb => ((route, options = {}) => send(engine, method, route, options, narrow, expected)) as Verb;
  return { get: verb('GET'), post: verb('POST'), put: verb('PUT'), patch: verb('PATCH'), delete: verb('DELETE') };
}

/**
 * The hub client, acting as whoever the context's session is (the tenant's admin on a hub with authentication on, the
 * implicit operator on one with it off): every request carries `X-Blizzard-Tenant`. It records what the test
 * creates in the ledger, narrows the lists `resources.ts` names to it at the levels that share a tenant, and fails the
 * test on an unexpected `5xx`.
 */
export function hubApi(options: {
  context: APIRequestContext;
  testInfo: TestInfo;
  /** Absent for a person signed in with no tenant bound: a headerless route settles on their one membership. */
  tenantId?: string;
  narrow: boolean;
  /** The level the test is served, which `sweep` reads to refuse an all-tenant pass below `fresh-hub`. */
  level: Level;
  ledger: Ledger;
  logContext: LogContext;
}): HubApi {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: options.tenantId === undefined ? {} : { 'X-Blizzard-Tenant': options.tenantId },
    ledger: options.ledger,
    logContext: options.logContext,
  };
  return {
    ...verbs(engine, options.narrow),
    async sweep(name: SweepName, sweepOptions?: { allTenants: true }): Promise<SweepPassResponse> {
      const allTenants = sweepOptions?.allTenants === true;
      if (allTenants) requireFreshHub(options.level, 'sweep(…, { allTenants: true })');
      const route = allTenants ? `/api/admin/sweeps/${name}/passes` : `/api/sweeps/${name}/passes`;
      const answer = await send(engine, 'POST', route, {}, false);
      const call = `sweep ${name}${allTenants ? ' over every tenant' : ''}`;
      if (answer.status === 409) {
        throw new Error(`${call} raced another pass of the sweep and was refused (409 sweep_in_progress): ${JSON.stringify(answer.body)}`);
      }
      const pass = answer.body as SweepPassResponse | undefined;
      if (!answer.ok || pass?.outcome !== 'completed') {
        throw new Error(`${call} did not complete: ${answer.status}, error ${JSON.stringify(pass?.error ?? answer.body)}`);
      }
      return pass;
    },
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

/** The mock runner's control plane, sent through the same timed and diagnosed request path as the hub. */
export function mockRunnerApi(options: { context: APIRequestContext; testInfo: TestInfo; url: string; ledger: Ledger }): {
  drive: <V extends string>(verb: V, body?: unknown) => Promise<ApiResponse<MockDriveAnswer<V>>>;
  lever: (kind: string, body?: unknown) => Promise<ApiResponse>;
  levers: () => Promise<ApiResponse<MockRunnerLeverListing>>;
} {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: {},
    ledger: options.ledger,
    logContext: () => '',
  };
  return {
    drive: (verb, body) => send(engine, 'POST', `${options.url}/_drive/${verb}`, { data: body }, false),
    lever: (kind, body) => send(engine, 'POST', `${options.url}/_levers/${kind}`, { data: body ?? {} }, false),
    levers: () => send(engine, 'GET', `${options.url}/_levers`, {}, false),
  };
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
