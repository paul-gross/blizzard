/** One request the generated client issued through the stubbed transport. */
export interface CapturedRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

/** Handle over a stubbed generated client — the captured requests plus a restore hook. */
export interface RequestClientStub {
  readonly requests: CapturedRequest[];
  /** The captured requests for one route + method. */
  forRoute(path: string, method?: string): CapturedRequest[];
  restore(): void;
}

/** A `route` return value naming a non-200 status — e.g. the hub's 404/409
 * `{"detail": "..."}` error bodies, or the runner's 503 volatile-route fault —
 * instead of the default 200 + JSON body. The one error convention both
 * generated clients' specs answer with (issue #81 — {@link stubRequestClient}
 * unifies the hub and runner twins, which previously answered with two
 * different conventions: a returned error value here, a thrown one there). */
export interface StubHttpError {
  readonly status: number;
  readonly body: unknown;
}

/** Build a {@link StubHttpError} a `route` callback can return to make
 * {@link stubRequestClient} answer with a non-200 status, e.g. the hub's 409
 * "no live route" / 404 "unknown chunk" responses, or the runner's 503
 * "store not wired" fault. */
export function stubError(status: number, body: unknown): StubHttpError {
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

/**
 * Stub a generated client's (hub or runner) transport with a fake `fetch` so
 * component tests can assert the exact call a button fires and hand back
 * canned responses — the TestBed-friendly seam (the Angular unit-test system
 * forbids `vi.mock` on relative imports). `route` maps a `METHOD /path` to the
 * JSON body to return (200), or to a {@link stubError} for a non-200 status;
 * unmatched routes return `{}` (200). Every request is captured for
 * assertions.
 *
 * Takes the generated client instance itself (`hubClient`/`runnerClient`) so
 * one helper serves both libraries' specs (issue #81) — the twin
 * `stubHubClient`/`stubRunnerClient` implementations this replaces differed
 * only in which client they configured and which error convention (return vs.
 * throw) they answered with.
 */
export function stubRequestClient(
  client: StubbableClient,
  route: (method: string, path: string) => unknown = () => ({}),
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
    requests.push({ method, path, body });
    const result = route(method, path);
    const [status, data] = isStubHttpError(result) ? [result.status, result.body] : [200, result];
    return new Response(JSON.stringify(data ?? {}), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  client.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });

  return {
    requests,
    forRoute: (path, method) =>
      requests.filter((r) => r.path === path && (method === undefined || r.method === method.toUpperCase())),
    restore: () => {
      client.setConfig({ baseUrl: '', fetch: previousFetch });
    },
  };
}
