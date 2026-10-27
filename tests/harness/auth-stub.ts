import * as http from 'node:http';

const JWKS_PATH = '/api/auth/jwks.json';

/** A request the stub received. */
export interface StubRequest {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
}

/** A loopback hub-auth stub: the one route a runner's hub-auth probe and its key cache read. */
export interface AuthStub {
  /** The base URL a runner's `hub_url` names. */
  url: string;
  /** The key set `jwks.json` answers from now on. */
  setKeys(keys: unknown[]): void;
  /** Every request received so far, in arrival order. */
  requests(): StubRequest[];
  close(): Promise<void>;
}

/**
 * Answers `GET /api/auth/jwks.json` with `{ keys }` (empty by default), so a runner whose `hub_url` names it probes hub
 * authentication as on. Every other path is `404`; the mock hub serves no auth route of its own.
 */
export async function startAuthStub(keys: unknown[] = []): Promise<AuthStub> {
  let served = keys;
  const received: StubRequest[] = [];
  const server = http.createServer((request, response) => {
    const route = (request.url ?? '').split('?')[0] ?? '';
    received.push({ method: request.method ?? '', path: route, headers: request.headers });
    request.resume();
    if (request.method === 'GET' && route === JWKS_PATH) {
      const body = JSON.stringify({ keys: served });
      response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) }).end(body);
      return;
    }
    response.writeHead(404, { 'content-length': '0' }).end();
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address() as { port: number };
  return {
    url: `http://127.0.0.1:${port}`,
    setKeys: (next) => {
      served = next;
    },
    requests: () => [...received],
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
