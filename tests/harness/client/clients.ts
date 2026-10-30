import type { APIRequestContext, TestInfo } from '@playwright/test';
import { type ApiResponse, type Engine, expecting, type Json, type LogContext, send, type Verbs, verbs } from './engine';
import type { Ledger } from './ledger';
import type { MockDriveAnswer, MockIssue, MockRepositoryCreated, MockRunnerLeverListing } from './mock';
import type { SweepPassResponse } from './wire/hub/types.gen';
import { identityFor } from '../auth';
import { requireFreshHub } from '../factories';
import type { Level } from '../tenancy';

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
    expecting: (status) => expecting(engine, options.narrow, status),
  };
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

/** Controls the test's own stub IdP; a lever would affect other tests on a shared hub. */
export function idpApi(options: { context: APIRequestContext; testInfo: TestInfo; url: string; level: Level; ledger: Ledger }): {
  refuseCallback: (refuse: boolean) => Promise<ApiResponse>;
  /** Makes the next dance that carries no `login_hint`, a browser's, resolve to the identity `identityFor` derives for `email`. */
  profile: (email: string) => Promise<ApiResponse>;
} {
  const lever = (route: string, data: unknown): Promise<ApiResponse> =>
    send({ context: options.context, testInfo: options.testInfo, headers: {}, ledger: options.ledger, logContext: () => '' },
      'PUT', `${options.url}${route}`, { data }, false);
  return {
    profile: (email) => {
      requireFreshHub(options.level, 'idp.profile()');
      return lever('/_levers/profile', identityFor(email));
    },
    refuseCallback: (refuse) => {
      requireFreshHub(options.level, 'idp.refuseCallback()');
      return send({ context: options.context, testInfo: options.testInfo, headers: {}, ledger: options.ledger, logContext: () => '' },
        'PUT', `${options.url}/_levers/refuse_callback`, { data: { refuse } }, false);
    },
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
