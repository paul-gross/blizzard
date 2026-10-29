import * as http from 'node:http';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import { type ApiResponse, type Engine, expecting, type LogContext, type Verbs, verbs } from './engine';
import type { Ledger } from './ledger';
import type {
  ArtifactScope,
  EscalationListResponse,
  FactListResponse,
  FindingView,
  DashboardView,
  FloorClearView,
  HeartbeatResponse,
  HostRunnerListResponse,
  HostSubscriptionListResponse,
  LeaseListResponse,
  LeaseUsageResponse,
  RunnerControlView,
  RunnerStatusView,
  TickPassResponse,
  TranscriptResponse,
  WorkerArtifact,
  WorkItemsView,
  TranscriptSegmentIndexView,
} from './wire/runner/types.gen';

/** The calls one runner of a host answers, under `<host>/runners/<id>`. Types come from `wire/runner`. */
export interface RunnerClient {
  readonly runnerId: string;
  /** `GET …/api/runner`. */
  status(): Promise<ApiResponse<RunnerStatusView>>;
  /** The outbound buffer's depth, read from the status's `hub.buffer_depth` and nothing else. */
  bufferDepth(): Promise<number>;
  leases(): Promise<ApiResponse<LeaseListResponse>>;
  /** `GET …/api/leases/{id}/usage`: the usage facts the lease recorded, oldest first. */
  leaseUsage(leaseId: string): Promise<ApiResponse<LeaseUsageResponse>>;
  /** `GET …/api/chunks/{id}/transcripts`: the chunk's transcript segments, as the runner indexes them. */
  transcriptSegments(chunkId: string): Promise<ApiResponse<TranscriptSegmentIndexView>>;
  /** `GET …/api/dashboard`. */
  dashboard(): Promise<ApiResponse<DashboardView>>;
  /** `POST …/api/heartbeat`: a heartbeat for a lease, as its worker's hook posts it. */
  heartbeat(leaseId: string): Promise<ApiResponse<HeartbeatResponse>>;
  /** The runner's facts as `FactView`s, unmapped: `seq`, `kind`, `chunk_id` and `acked_at` survive as the runner sent them. */
  facts(options?: { limit?: number }): Promise<ApiResponse<FactListResponse>>;
  /** `GET …/api/escalations`: the open set by default; `state: 'closed'` pages the ended ones, each with its closure. */
  escalations(options?: { state?: 'open' | 'closed'; limit?: number }): Promise<ApiResponse<EscalationListResponse>>;
  /** `GET …/api/leases/{id}/artifacts`, narrowed to one `scope`; `headers` act as the lease's worker (`workerHeaders`). */
  leaseArtifacts(leaseId: string, options?: { scope?: ArtifactScope; headers?: Record<string, string> }): Promise<ApiResponse<WorkerArtifact[]>>;
  /** `GET …/api/leases/{id}/garden/findings`: the routine run's live finding bucket; `headers` act as the lease's worker. */
  leaseGardenFindings(leaseId: string, options?: { headers?: Record<string, string> }): Promise<ApiResponse<FindingView[]>>;
  /** `GET …/api/leases/{id}/transcript`: the lease's resolved transcript. */
  leaseTranscript(leaseId: string, options?: { headers?: Record<string, string> }): Promise<ApiResponse<TranscriptResponse>>;
  /** `GET …/api/leases/{id}/analytics/{kind}/{dimension}`, proxied to the hub; `headers` act as the lease's worker (`workerHeaders`). */
  leaseAnalytics(leaseId: string, kind: 'counts' | 'spend', dimension: string, options?: { since?: string; params?: Record<string, string>; headers?: Record<string, string> }): Promise<ApiResponse<Record<string, unknown>>>;
  /** `GET …/api/chunks/{id}/work-items`, proxied to the hub; `headers` act as the chunk's worker (`workerHeaders`). */
  workItems(chunkId: string, options?: { headers?: Record<string, string> }): Promise<ApiResponse<WorkItemsView>>;
  /** `GET …/api/leases/{id}/garden/proposals`, proxied to the hub; `headers` act as the lease's worker (`workerHeaders`). */
  leaseGardenProposals(leaseId: string, options?: { params?: Record<string, string>; headers?: Record<string, string> }): Promise<ApiResponse<GardenProposalRow[]>>;
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

/** A garden proposal as the runner proxies it from the hub: the fields the worker's read is asserted on. */
export interface GardenProposalRow {
  proposal_id: string;
  routine_name: string;
  findings: string[];
  closure?: { closure: string; reason: string } | null;
}

/** A telemetry signal a worker exports to its runner, `POST /runners/{id}/v1/{signal}`. */
export type TelemetrySignal = 'traces' | 'metrics' | 'logs';

/** One telemetry export: its body, the content type it declares, and the worker's headers that carry its lease token. */
export interface TelemetryExport {
  body: Uint8Array | string;
  contentType: string;
  /** The worker's credential, as `workerHeaders` gives it. */
  headers: Record<string, string>;
}

/** The host's routes that answer the same over TCP and over its Unix socket: a spec loops over the two transports. */
export interface HostRoutes {
  /** `GET /api/health`, with `params` as its query. */
  health(params?: Record<string, string>): Promise<ApiResponse>;
  /** `POST /runners/{id}/v1/{signal}`: a worker's OTLP export to the runner that leased it. */
  exportTelemetry(runnerId: string, signal: TelemetrySignal, telemetry: TelemetryExport): Promise<ApiResponse>;
}

/** The calls the runner host answers for itself, and the way to one of its runners. */
export interface RunnerHostClient extends HostRoutes {
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
    health: (params) => host.get('/api/health', params === undefined ? {} : { params }),
    exportTelemetry: (runnerId, signal, telemetry) =>
      host.post(`/runners/${encodeURIComponent(runnerId)}/v1/${signal}`, {
        data: Buffer.from(telemetry.body),
        headers: { 'content-type': telemetry.contentType, ...telemetry.headers },
      }),
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
        leaseUsage: (leaseId) => raw.get(`/api/leases/${encodeURIComponent(leaseId)}/usage`),
        transcriptSegments: (chunkId) => raw.get(`/api/chunks/${encodeURIComponent(chunkId)}/transcripts`),
        dashboard: () => raw.get('/api/dashboard'),
        heartbeat: (leaseId) => raw.post('/api/heartbeat', { data: { lease_id: leaseId } }),
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
        leaseGardenFindings: (leaseId, findingOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/garden/findings`, { headers: findingOptions.headers }),
        leaseTranscript: (leaseId, transcriptOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/transcript`, { headers: transcriptOptions.headers }),
        leaseAnalytics: (leaseId, kind, dimension, analyticsOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/analytics/${kind}/${encodeURIComponent(dimension)}`, {
            params: { since: analyticsOptions.since ?? '2020-01-01T00:00:00Z', ...analyticsOptions.params },
            headers: analyticsOptions.headers,
          }),
        workItems: (chunkId, itemOptions = {}) => raw.get(`/api/chunks/${encodeURIComponent(chunkId)}/work-items`, { headers: itemOptions.headers }),
        leaseGardenProposals: (leaseId, proposalsOptions = {}) =>
          raw.get(`/api/leases/${encodeURIComponent(leaseId)}/garden/proposals`, {
            params: proposalsOptions.params,
            headers: proposalsOptions.headers,
          }),
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

/** The routes a runner host answers over its Unix socket: the step routes, and those it answers over TCP too. */
export interface HostSocketClient extends HostRoutes {
  /** `POST /runners/{id}/api/ticks`: one pass of one runner, answered once the pass has completed. */
  stepRunner(runnerId: string): Promise<TickPass>;
  /** `POST /api/host/ticks`: one pass of the host's own sweeps, answered once the pass has completed. */
  stepHost(): Promise<TickPass>;
}

/**
 * The host's routes over its Unix socket, which the human-lane gate always admits, so stepping works on a host whose
 * hub offers an IdP. A request has no client timeout of its own: the test's bounds it. A step answered other than `200`
 * rejects with the route, the status and body, and the host log from `logContext`; the shared routes answer as their TCP
 * form does, any status returned.
 */
export function hostSocketApi(options: { socket: string; logContext: LogContext }): HostSocketClient {
  const send = (method: string, route: string, headers: Record<string, string>, body?: Uint8Array | string): Promise<{ status: number; text: string; headers: Record<string, string> }> =>
    new Promise((resolve, reject) => {
      const payload = body === undefined ? undefined : Buffer.from(body);
      const request = http.request({ socketPath: options.socket, path: route, method, headers: { 'content-length': String(payload?.length ?? 0), ...headers } }, (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('end', () =>
          resolve({
            status: response.statusCode ?? 0,
            text: Buffer.concat(chunks).toString('utf8'),
            headers: Object.fromEntries(Object.entries(response.headers).map(([name, value]) => [name, [value].flat().join(', ')])),
          }),
        );
      });
      request.on('error', (error) => reject(new Error(`${method} ${route} over ${options.socket} failed: ${error.message}\nhost log:\n${options.logContext()}`, { cause: error })));
      request.end(payload);
    });
  const answered = async (method: string, route: string, headers: Record<string, string> = {}, body?: Uint8Array | string): Promise<ApiResponse> => {
    const answer = await send(method, route, headers, body);
    let parsed: unknown = answer.text;
    try {
      parsed = answer.text === '' ? undefined : JSON.parse(answer.text);
    } catch {
      // Not JSON: the text stands.
    }
    return { status: answer.status, ok: answer.status >= 200 && answer.status < 300, body: parsed, headers: answer.headers };
  };
  const post = async (route: string): Promise<TickPass> => {
    const answer = await send('POST', route, {});
    if (answer.status === 200) return JSON.parse(answer.text) as TickPass;
    throw new Error(`POST ${route} over ${options.socket} answered ${answer.status}: ${answer.text}\nhost log:\n${options.logContext()}`);
  };
  return {
    health: (params) => answered('GET', `/api/health${params === undefined ? '' : `?${new URLSearchParams(params)}`}`),
    exportTelemetry: (runnerId, signal, telemetry) =>
      answered('POST', `/runners/${encodeURIComponent(runnerId)}/v1/${signal}`, { 'content-type': telemetry.contentType, ...telemetry.headers }, telemetry.body),
    stepRunner: (runnerId) => post(`/runners/${encodeURIComponent(runnerId)}/api/ticks`),
    stepHost: () => post('/api/host/ticks'),
  };
}
