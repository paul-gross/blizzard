import { client } from '../api/hub/client.gen';

/** One request the generated hub client issued through the stubbed transport. */
export interface CapturedRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

/** Handle over a stubbed hub client — the captured requests plus a restore hook. */
export interface HubClientStub {
  readonly requests: CapturedRequest[];
  /** The captured requests for one route + method. */
  forRoute(path: string, method?: string): CapturedRequest[];
  restore(): void;
}

/**
 * Stub the generated hub client's transport with a fake `fetch` so component tests
 * can assert the exact call a button fires and hand back canned responses — the
 * TestBed-friendly seam (the Angular unit-test system forbids `vi.mock` on relative
 * imports). `route` maps a `METHOD /path` to the JSON body to return; unmatched
 * routes return `{}`. Every request is captured for assertions.
 */
export function stubHubClient(route: (method: string, path: string) => unknown = () => ({})): HubClientStub {
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
    const data = route(method, path);
    return new Response(JSON.stringify(data ?? {}), {
      status: 200,
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
