import type { APIRequestContext, TestInfo } from '@playwright/test';
import { type ApiResponse, type Engine, expecting, type Json, type LogContext, send, type Verbs, verbs } from './engine';
import type { Ledger } from './ledger';
import type {
  MockCapturedRequest,
  MockChunkSeeded,
  MockChunkSpec,
  MockHubLeverListing,
  MockProjectSeeded,
  MockRunnerSeed,
  MockTenantSeeded,
} from './mock';
import type { HostAddRequest, HostAddResponse, RunnerAddRequest, RunnerAddResponse, RunnerEnrollmentResponse } from './wire/hub/types.gen';

/** The mock hub's control plane, enrollment routes, and a fleet client for any token: everything its README documents. */
export interface MockHubClient {
  readonly url: string;
  /** `/_seed/*`: the state a scenario starts from. */
  readonly seed: {
    chunk(spec: MockChunkSpec): Promise<ApiResponse<MockChunkSeeded>>;
    tenant(body: { name: string; tenant_id?: string }): Promise<ApiResponse<MockTenantSeeded>>;
    project(body: { slug: string; name: string; tenant_id?: string; project_id?: string }): Promise<ApiResponse<MockProjectSeeded>>;
    runner(body: MockRunnerSeed): Promise<ApiResponse<RunnerAddResponse>>;
    retireRunner(runnerId: string, by: string): Promise<ApiResponse>;
    revokeRunnerToken(runnerId: string): Promise<ApiResponse>;
    pauseRunner(runnerId: string, paused: boolean): Promise<ApiResponse>;
    runnerProjects(runnerId: string, projects: string[]): Promise<ApiResponse>;
    scope(body: { slug: string; description?: string; retired?: boolean }): Promise<ApiResponse>;
    /** Publishes (or replaces) one global system-scope document by name. */
    systemArtifact(body: { name: string; content: string }): Promise<ApiResponse>;
    answer(body: { question_id: string; answer: string; answered_by?: string }): Promise<ApiResponse>;
    stop(chunkId: string): Promise<ApiResponse>;
    /** Clears every runner, token, chunk, and tenant: the test's own mock hub is the only one this reaches. */
    reset(): Promise<ApiResponse>;
  };
  /** `/_levers`: the transport and state levers. */
  readonly levers: {
    list(): Promise<ApiResponse<MockHubLeverListing>>;
    arm(kind: string, options?: { chunk_id?: string; remaining?: number; payload?: Record<string, unknown> }): Promise<ApiResponse>;
    clear(kind: string, chunkId?: string): Promise<ApiResponse>;
    reset(): Promise<ApiResponse>;
  };
  /** `/_captured`: every fleet-facing request received, with its headers. */
  readonly captured: {
    read(): Promise<ApiResponse<{ requests: MockCapturedRequest[] }>>;
    reset(): Promise<ApiResponse>;
  };
  /** Enrollment, as an operator does it: the one place a token is minted or rotated. */
  readonly enroll: {
    host(body: HostAddRequest): Promise<ApiResponse<HostAddResponse>>;
    runner(body: RunnerAddRequest): Promise<ApiResponse<RunnerAddResponse>>;
    rotate(runnerId: string): Promise<ApiResponse<RunnerEnrollmentResponse>>;
  };
  /** The `/api/fleet` plane under a bearer token. A `5xx` fails the test with the call and the mock hub's log. */
  fleet(token: string): Verbs & { expecting(status: number): Verbs };
  /** Raw verbs over the mock hub's own URL, for a route not yet named. */
  readonly raw: Verbs;
}

/**
 * A client for a test's own mock hub at `url`. Every call goes through the engine, so it is timed and a `5xx` fails the
 * test with the call and `logContext` attached. The control plane is exempt from the transport levers in the mock, so
 * seeding and arming never trip that; a fleet call during an armed `unreachable` lever does, unless it goes through
 * `expecting(503)`.
 */
export function mockHubApi(options: { context: APIRequestContext; testInfo: TestInfo; url: string; ledger: Ledger; logContext: LogContext }): MockHubClient {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: {},
    ledger: options.ledger,
    logContext: options.logContext,
    logName: 'mock hub',
  };
  const call = <T = Json>(method: string, route: string, data?: unknown, params?: Record<string, string | number | boolean>): Promise<ApiResponse<T>> =>
    send(engine, method, `${options.url}${route}`, { data, params }, false) as Promise<ApiResponse<T>>;
  // The engine's verbs take a full URL for a route that names one; these take the path under the mock hub.
  const atUrl = (inner: Verbs): Verbs => {
    const under = (verb: keyof Verbs): Verbs[typeof verb] => ((route, callOptions) => inner[verb](`${options.url}${route}`, callOptions)) as Verbs[typeof verb];
    return { get: under('get'), post: under('post'), put: under('put'), patch: under('patch'), delete: under('delete') };
  };
  return {
    url: options.url,
    seed: {
      chunk: (spec) => call('POST', '/_seed/chunk', spec),
      tenant: (body) => call('POST', '/_seed/tenants', body),
      project: (body) => call('POST', '/_seed/projects', body),
      runner: (body) => call('POST', '/_seed/runners', body),
      retireRunner: (runnerId, by) => call('POST', `/_seed/runners/${runnerId}/retire`, { by }),
      revokeRunnerToken: (runnerId) => call('POST', `/_seed/runners/${runnerId}/token-revocations`),
      pauseRunner: (runnerId, paused) => call('POST', `/_seed/runners/${runnerId}/pause`, undefined, { paused }),
      runnerProjects: (runnerId, projects) => call('PUT', `/_seed/runners/${runnerId}/projects`, { projects }),
      scope: (body) => call('POST', '/_seed/scopes', body),
      systemArtifact: (body) => call('POST', '/_seed/system-artifacts', body),
      answer: (body) => call('POST', '/_seed/answer', body),
      stop: (chunkId) => call('POST', '/_seed/stop', { chunk_id: chunkId }),
      reset: () => call('POST', '/_seed/reset'),
    },
    levers: {
      list: () => call('GET', '/_levers'),
      arm: (kind, armOptions = {}) => call('POST', `/_levers/${kind}`, armOptions),
      clear: (kind, chunkId) => call('DELETE', `/_levers/${kind}`, undefined, chunkId === undefined ? undefined : { chunk_id: chunkId }),
      reset: () => call('POST', '/_levers/reset'),
    },
    captured: {
      read: () => call('GET', '/_captured'),
      reset: () => call('POST', '/_captured/reset'),
    },
    enroll: {
      host: (body) => call('POST', '/api/hosts', body),
      runner: (body) => call('POST', '/api/runners', body),
      rotate: (runnerId) => call('POST', `/api/runners/${runnerId}/enrollments`),
    },
    fleet: (token) => {
      const fleetEngine: Engine = { ...engine, headers: { Authorization: `Bearer ${token}` } };
      return { ...atUrl(verbs(fleetEngine, false)), expecting: (status) => atUrl(expecting(fleetEngine, false, status)) };
    },
    raw: atUrl(verbs(engine, false)),
  };
}
