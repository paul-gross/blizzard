import * as http from 'node:http';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import { type ApiResponse, type Engine, expecting, type LogContext, type Verbs, verbs } from './engine';
import type { Ledger } from './ledger';
import type {
  ArtifactScope,
  EscalationListResponse,
  FactListResponse,
  FloorClearView,
  HostRunnerListResponse,
  HostSubscriptionListResponse,
  LeaseListResponse,
  RunnerControlView,
  RunnerStatusView,
  TickPassResponse,
  TranscriptResponse,
  WorkerArtifact,
} from './wire/runner/types.gen';

/** The calls one runner of a host answers, under `<host>/runners/<id>`. Types come from `wire/runner`. */
export interface RunnerClient {
  readonly runnerId: string;
  /** `GET …/api/runner`. */
  status(): Promise<ApiResponse<RunnerStatusView>>;
  /** The outbound buffer's depth, read from the status's `hub.buffer_depth` and nothing else. */
  bufferDepth(): Promise<number>;
  leases(): Promise<ApiResponse<LeaseListResponse>>;
  /** The runner's facts as `FactView`s, unmapped: `seq`, `kind`, `chunk_id` and `acked_at` survive as the runner sent them. */
  facts(options?: { limit?: number }): Promise<ApiResponse<FactListResponse>>;
  /** `GET …/api/escalations`: the open set by default; `state: 'closed'` pages the ended ones, each with its closure. */
  escalations(options?: { state?: 'open' | 'closed'; limit?: number }): Promise<ApiResponse<EscalationListResponse>>;
  /** `GET …/api/leases/{id}/artifacts`, narrowed to one `scope`; `headers` act as the lease's worker (`workerHeaders`). */
  leaseArtifacts(leaseId: string, options?: { scope?: ArtifactScope; headers?: Record<string, string> }): Promise<ApiResponse<WorkerArtifact[]>>;
  /** `GET …/api/leases/{id}/transcript`: the lease's resolved transcript. */
  leaseTranscript(leaseId: string, options?: { headers?: Record<string, string> }): Promise<ApiResponse<TranscriptResponse>>;
  /** `PATCH …/api/runner`: the runner's local pause, or its release. */
  setPaused(paused: boolean, by?: string): Promise<ApiResponse<RunnerControlView>>;
  pauseChunk(chunkId: string): Promise<ApiResponse>;
  resumeChunk(chunkId: string): Promise<ApiResponse>;
  requeueChunk(chunkId: string): Promise<ApiResponse>;
  /** `POST …/api/runner/floor/clear`. */
  clearFloor(by?: string): Promise<ApiResponse<FloorClearView>>;
  /** The same client for one call whose `5xx` comes back to the test instead of failing it. */
  expecting(status: number): RunnerClient;
  /** Raw verbs under the runner's prefix, for a route not yet named. */
  readonly raw: Verbs;
}

/** The calls the runner host answers for itself, and the way to one of its runners. */
export interface RunnerHostClient {
  readonly url: string;
  /** `GET /api/host/runners`. */
  runners(): Promise<ApiResponse<HostRunnerListResponse>>;
  /** `GET /api/host/subscriptions`. */
  subscriptions(): Promise<ApiResponse<HostSubscriptionListResponse>>;
  /** `POST /api/host/subscriptions/{slug}/floor/clear`. */
  clearSubscriptionFloor(slug: string, by?: string): Promise<ApiResponse<FloorClearView>>;
  runner(runnerId: string): RunnerClient;
  expecting(status: number): RunnerHostClient;
  readonly raw: Verbs;
}

/**
 * A client for a runner host's local API at `url`, over TCP. Every call goes through the engine, so it is timed and a
 * `5xx` fails the test with the call and `logContext` attached; the runner host's log is whatever the caller names, since
 * the host is not the client's to start. `headers` carry a session when the host is under hub authentication.
 */
export function runnerHostApi(options: {
  context: APIRequestContext;
  testInfo: TestInfo;
  url: string;
  ledger: Ledger;
  logContext: LogContext;
  headers?: Record<string, string>;
  /** The status a `5xx` is expected with; the client `expecting` returns sets it. */
  expected?: number;
}): RunnerHostClient {
  const engine: Engine = {
    context: options.context,
    testInfo: options.testInfo,
    headers: options.headers ?? {},
    ledger: options.ledger,
    logContext: options.logContext,
    logName: 'runner host',
  };
  const under = (prefix: string): Verbs => {
    const inner = options.expected === undefined ? verbs(engine, false) : expecting(engine, false, options.expected);
    const at = (verb: keyof Verbs): Verbs[typeof verb] => ((route, callOptions) => inner[verb](`${options.url}${prefix}${route}`, callOptions)) as Verbs[typeof verb];
    return { get: at('get'), post: at('post'), put: at('put'), patch: at('patch'), delete: at('delete') };
  };
  const host = under('');
  return {
    url: options.url,
    runners: () => host.get('/api/host/runners'),
    subscriptions: () => host.get('/api/host/subscriptions'),
    clearSubscriptionFloor: (slug, by) => host.post(`/api/host/subscriptions/${encodeURIComponent(slug)}/floor/clear`, { data: by === undefined ? {} : { by } }),
    runner(runnerId) {
      const raw = under(`/runners/${runnerId}`);
      const status = (): Promise<ApiResponse<RunnerStatusView>> => raw.get('/api/runner');
      const chunk = (chunkId: string, verb: string): Promise<ApiResponse> => raw.post(`/api/chunks/${chunkId}/${verb}`);
      return {
        runnerId,
        status,
        async bufferDepth() {
          const answer = await status();
          if (!answer.ok) throw new Error(`reading the status of runner ${runnerId} answered ${answer.status}: ${JSON.stringify(answer.body)}`);
          return answer.body.hub.buffer_depth;
        },
        leases: () => raw.get('/api/leases'),
        facts: (factOptions = {}) => raw.get('/api/facts', factOptions.limit === undefined ? {} : { params: { limit: factOptions.limit } }),
        escalations: (listOptions = {}) => {
          const params = Object.fromEntries(Object.entries(listOptions).filter(([, value]) => value !== undefined)) as Record<string, string | number>;
          return raw.get('/api/escalations', { params });
        },
        leaseArtifacts: (leaseId, artifactOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/artifacts`, {
            params: artifactOptions.scope === undefined ? {} : { scope: artifactOptions.scope },
            headers: artifactOptions.headers,
          }),
        leaseTranscript: (leaseId, transcriptOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/transcript`, { headers: transcriptOptions.headers }),
        setPaused: (paused, by) => raw.patch('/api/runner', { data: by === undefined ? { paused } : { paused, by } }),
        pauseChunk: (chunkId) => chunk(chunkId, 'pause'),
        resumeChunk: (chunkId) => chunk(chunkId, 'resume'),
        requeueChunk: (chunkId) => chunk(chunkId, 'requeues'),
        clearFloor: (by) => raw.post('/api/runner/floor/clear', { data: by === undefined ? {} : { by } }),
        expecting: (status) => runnerHostApi({ ...options, expected: status }).runner(runnerId),
        raw,
      };
    },
    expecting: (status) => runnerHostApi({ ...options, expected: status }),
    raw: host,
  };
}

/** The pass a step ran: when it started and finished, how it ended, and the error a failed pass recorded. */
export type TickPass = TickPassResponse;

/** The step routes a runner host answers over its Unix socket. */
export interface HostSocketClient {
  /** `POST /runners/{id}/api/ticks`: one pass of one runner, answered once the pass has completed. */
  stepRunner(runnerId: string): Promise<TickPass>;
  /** `POST /api/host/ticks`: one pass of the host's own sweeps, answered once the pass has completed. */
  stepHost(): Promise<TickPass>;
}

/**
 * The step routes over the host's Unix socket, which the human-lane gate always admits, so stepping works on a host whose
 * hub offers an IdP. A request has no client timeout of its own: the test's bounds it. An answer other than `200`
 * rejects with the route, the status and body, and the host log from `logContext`.
 */
export function hostSocketApi(options: { socket: string; logContext: LogContext }): HostSocketClient {
  const post = (route: string): Promise<TickPass> =>
    new Promise((resolve, reject) => {
      const request = http.request({ socketPath: options.socket, path: route, method: 'POST', headers: { 'content-length': '0' } }, (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          if (response.statusCode === 200) return resolve(JSON.parse(text) as TickPass);
          reject(new Error(`POST ${route} over ${options.socket} answered ${response.statusCode}: ${text}\nhost log:\n${options.logContext()}`));
        });
      });
      request.on('error', (error) => reject(new Error(`POST ${route} over ${options.socket} failed: ${error.message}\nhost log:\n${options.logContext()}`, { cause: error })));
      request.end();
    });
  return {
    stepRunner: (runnerId) => post(`/runners/${encodeURIComponent(runnerId)}/api/ticks`),
    stepHost: () => post('/api/host/ticks'),
  };
}
