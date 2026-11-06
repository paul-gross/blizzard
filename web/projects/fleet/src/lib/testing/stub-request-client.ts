import { client as hubClient } from '../api/hub/client.gen';
import { client as runnerClient } from '../api/runner/client.gen';
import type { AnswerResult } from '../api/hub';
import type { HubOperations } from './hub-operations.gen';
import type { RunnerOperations } from './runner-operations.gen';

/** One request the generated client issued through the stubbed transport. */
export interface CapturedRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
  /** The URL's query string, `?`-prefixed, or empty. */
  readonly search: string;
  /** The request headers, names lower-cased. */
  readonly headers: Readonly<Record<string, string>>;
}

/** Handle over a stubbed generated client — the captured requests plus a restore hook. */
export interface RequestClientStub {
  readonly requests: CapturedRequest[];
  /** The captured requests for one route + method. */
  forRoute(path: string, method?: string): CapturedRequest[];
  restore(): void;
}

/** A `route` return value naming a non-200 status instead of the
 * default 200 + JSON body. */
export interface StubHttpError<S extends number = number, B = unknown> {
  readonly status: S;
  readonly body: B;
}

/** Build a {@link StubHttpError} a `route` callback can return to make
 * the stub answer with a non-200 status, e.g. the hub's 409
 * "no live route" / 404 "unknown chunk" responses, or the runner's 503
 * "store not wired" fault. */
export function stubError<S extends number, B>(status: S, body: B): StubHttpError<S, B> {
  return { status, body };
}

function isStubHttpError(value: unknown): value is StubHttpError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    'body' in value &&
    typeof (value as { status: unknown }).status === 'number'
  );
}

/** The one method a generated `@hey-api` client instance needs for stubbing —
 * narrow on purpose so this helper stays agnostic to which client (hub or
 * runner) it stubs, rather than importing either generated module's types. */
interface StubbableClient {
  setConfig(config: { baseUrl: string; fetch: typeof fetch }): unknown;
}

/** A refusal whose body the hub's OpenAPI document does not declare. It cannot be built freehand: each undeclared
 * wire shape gets its own typed constructor (below), so the body is still checked against a generated type. */
export interface StubUndeclaredError extends StubHttpError<number, unknown> {
  readonly undeclared: true;
}

/** The lost answer race: a 409 whose body is the winning answer, which the hub sends but does not declare. */
export function stubAnswerRaceLost(winner: AnswerResult): StubUndeclaredError {
  return { status: 409, body: winner, undeclared: true };
}

/** The body FastAPI's `HTTPException` sends — the envelope every domain refusal rides, declared or not. */
export interface StubDetailBody {
  readonly detail: string;
}

/** One operation's entry in a generated operation table (`hub-operations.gen.ts`, `runner-operations.gen.ts`). */
interface StubOperation {
  readonly path: unknown;
  readonly responses: object;
  readonly errors: object;
}

/** The operation's success body — the 200 response, else whichever success status it declares. */
type SuccessBody<R> = R extends { 200: infer B } ? B : R[keyof R];

/** The operation's path params, resolved from its template; an operation without any gets an empty record. */
type RouteParams<P> = [P] extends [undefined] ? Record<never, never> : NonNullable<P>;

/** A refusal the operation can answer with: a declared status carrying its declared body, or any status carrying the
 * `{ detail }` envelope (a 422 the document models as a validation list is sent as a string by domain refusals). */
type RouteError<E> =
  | { [S in keyof E & number]: StubHttpError<S, E[S]> }[keyof E & number]
  | StubHttpError<number, StubDetailBody>
  | StubUndeclaredError;

/** What a route handler returns, sync or async: the operation's success body or one of its refusals. */
type RouteResult<O extends StubOperation> = SuccessBody<O['responses']> | RouteError<O['errors']>;

/** One route's handler — the captured request plus the path params its template resolved. */
export type StubRouteHandler<O extends StubOperation> = (
  request: CapturedRequest,
  params: RouteParams<O['path']>,
) => RouteResult<O> | Promise<RouteResult<O>>;

/** The routes a spec declares: any subset of a plane's operations, keyed `'METHOD /templated/url'`. */
export type StubRoutes<Ops extends { [K in keyof Ops]: StubOperation }> = {
  [K in keyof Ops]?: StubHandler<Ops[K]>;
};
type StubHandler<O> = O extends StubOperation ? StubRouteHandler<O> : never;

interface CompiledRoute {
  readonly method: string;
  readonly segments: readonly string[];
  readonly literals: number;
  readonly handler: (request: CapturedRequest, params: Record<string, string>) => unknown;
}

function compileRoutes(routes: Record<string, unknown>): CompiledRoute[] {
  return Object.entries(routes).flatMap(([key, handler]) => {
    if (typeof handler !== 'function') return [];
    const space = key.indexOf(' ');
    const segments = key.slice(space + 1).split('/');
    return [
      {
        method: key.slice(0, space),
        segments,
        literals: segments.filter((segment) => !/^\{.+\}$/.test(segment)).length,
        handler: handler as CompiledRoute['handler'],
      },
    ];
  });
}

/** Resolve the route a request matches — `{param}` matches exactly one segment; with several matches, the most literal wins. */
function matchRoute(
  routes: readonly CompiledRoute[],
  method: string,
  path: string,
): { route: CompiledRoute; params: Record<string, string> } | undefined {
  const parts = path.split('/');
  let best: { route: CompiledRoute; params: Record<string, string> } | undefined;
  for (const route of routes) {
    if (route.method !== method || route.segments.length !== parts.length) continue;
    const params: Record<string, string> = {};
    const matches = route.segments.every((segment, i) => {
      const param = /^\{(.+)\}$/.exec(segment);
      if (param) {
        params[param[1]] = decodeURIComponent(parts[i]);
        return parts[i] !== '';
      }
      return segment === parts[i];
    });
    if (matches && (best === undefined || route.literals > best.route.literals)) best = { route, params };
  }
  return best;
}

/**
 * Stub a generated client's transport with a fake `fetch`, so component tests
 * can assert the exact call a button fires and hand back canned responses —
 * the TestBed-friendly seam (the Angular unit-test system forbids `vi.mock` on
 * relative imports). Each route is a handler keyed by the operation it serves
 * (`'GET /api/chunks/{chunk_id}'`); its success body, path params and refusals
 * are typed from the generated operation, so a wrong or incomplete canned body
 * fails the spec's compile. A request matching no declared route is answered
 * 404, never a made-up body. Every request is captured for assertions.
 */
function stubClient(
  client: StubbableClient,
  routes: Record<string, unknown>,
): RequestClientStub {
  const compiled = compileRoutes(routes);
  return stubTransport(client, async (request) => {
    const match = matchRoute(compiled, request.method, request.path);
    if (!match) {
      return stubError(404, { detail: `No stub route for ${request.method} ${request.path}` });
    }
    return match.route.handler(request, match.params);
  });
}

/** Stub the hub client (`hubClient`) with typed routes — see {@link StubRoutes}. */
export function stubHubClient(routes: StubRoutes<HubOperations>): RequestClientStub {
  return stubClient(hubClient, routes);
}

/** Stub the runner client (`runnerClient`) with typed routes — see {@link StubRoutes}. */
export function stubRunnerClient(routes: StubRoutes<RunnerOperations>): RequestClientStub {
  return stubClient(runnerClient, routes);
}

/**
 * The shared transport: a fake `fetch` on a generated client that captures every request and answers with whatever
 * `route` returns — a body (200) or a {@link StubHttpError}. The typed entry points are the only callers.
 */
function stubTransport(
  client: StubbableClient,
  route: (request: CapturedRequest) => unknown | Promise<unknown>,
): RequestClientStub {
  const requests: CapturedRequest[] = [];
  const previousFetch = globalThis.fetch;

  const fakeFetch = async (input: Request): Promise<Response> => {
    const url = new URL(input.url);
    const path = url.pathname;
    const method = input.method.toUpperCase();
    let body: unknown;
    try {
      const text = await input.clone().text();
      body = text ? JSON.parse(text) : undefined;
    } catch {
      body = undefined;
    }
    const captured: CapturedRequest = {
      method,
      path,
      body,
      search: url.search,
      headers: Object.fromEntries(input.headers.entries()),
    };
    requests.push(captured);
    const result = await route(captured);
    const [status, data] = isStubHttpError(result) ? [result.status, result.body] : [200, result];
    return new Response(JSON.stringify(data ?? {}), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  client.setConfig({
    baseUrl: 'http://localhost',
    fetch: fakeFetch as typeof fetch,
  });

  return {
    requests,
    forRoute: (path, method) =>
      requests.filter((r) => r.path === path && (method === undefined || r.method === method.toUpperCase())),
    restore: () => {
      client.setConfig({ baseUrl: '', fetch: previousFetch });
    },
  };
}
