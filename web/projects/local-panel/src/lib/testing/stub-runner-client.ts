import { runnerClient } from 'fleet';

/** One request the generated runner client issued through the stubbed transport. */
export interface CapturedRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

/** Handle over a stubbed runner client — the captured requests plus a restore hook. */
export interface RunnerClientStub {
  readonly requests: CapturedRequest[];
  /** The captured requests for one route + method. */
  forRoute(path: string, method?: string): CapturedRequest[];
  restore(): void;
}

/**
 * Thrown by a `route` callback to make the stub answer with a non-2xx status —
 * `GET /api/leases` is a volatile, store-free-capable route (issue #28) that
 * genuinely 503s, and the panel's degraded path (`isError()`, `data-testid`
 * `error-state`) needs a way to exercise that without the empty-state read.
 */
export class RouteError extends Error {
  constructor(
    readonly status: number,
    message = `stubbed route error (${status})`,
  ) {
    super(message);
  }
}

/**
 * Stub the generated runner client's transport with a fake `fetch` so component
 * tests can assert the exact call the local panel fires and hand back canned
 * responses — modeled on `fleet`'s `stubHubClient` (the TestBed-friendly seam;
 * the Angular unit-test system forbids `vi.mock` on relative imports). `route`
 * maps a `METHOD /path` to the JSON body to return (200), or throws a
 * {@link RouteError} for a non-2xx response; unmatched routes return `{}`.
 * Every request is captured for assertions.
 */
export function stubRunnerClient(route: (method: string, path: string) => unknown = () => ({})): RunnerClientStub {
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

    let status = 200;
    let data: unknown;
    try {
      data = route(method, path);
    } catch (err) {
      if (!(err instanceof RouteError)) throw err;
      status = err.status;
      data = { detail: err.message };
    }

    return new Response(JSON.stringify(data ?? {}), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  runnerClient.setConfig({ baseUrl: 'http://localhost', fetch: fakeFetch as typeof fetch });

  return {
    requests,
    forRoute: (path, method) =>
      requests.filter((r) => r.path === path && (method === undefined || r.method === method.toUpperCase())),
    restore: () => {
      runnerClient.setConfig({ baseUrl: '', fetch: previousFetch });
    },
  };
}
