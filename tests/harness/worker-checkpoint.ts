import * as http from 'node:http';
import type { AddressInfo } from 'node:net';

/**
 * A loopback checkpoint in the test's own worker: a place a mock worker stops until the test lets it go. The test puts
 * `snippet(name)` in a node prompt; the worker that runs it announces its arrival and blocks on the answer, so a lease
 * stays open for exactly as long as the test holds it, with no clock on either side.
 */
export interface WorkerCheckpoint {
  /** A Python snippet for a node prompt: arrives at the named checkpoint, then blocks until `release(name)`. */
  snippet(name: string): string;
  /** Resolves once a worker is at the named checkpoint; resolves at once if one already arrived. */
  reached(name: string): Promise<void>;
  /** Lets the worker at the named checkpoint go on; a worker arriving later passes straight through. */
  release(name: string): void;
  /** Releases every worker still held, then stops serving. */
  close(): Promise<void>;
}

interface Point {
  arrived: Promise<void>;
  arrive(): void;
  released: boolean;
  held: http.ServerResponse[];
}

export async function startWorkerCheckpoint(): Promise<WorkerCheckpoint> {
  const points = new Map<string, Point>();
  const point = (name: string): Point => {
    let found = points.get(name);
    if (found === undefined) {
      let arrive!: () => void;
      const arrived = new Promise<void>((resolve) => (arrive = resolve));
      found = { arrived, arrive, released: false, held: [] };
      points.set(name, found);
    }
    return found;
  };
  const answer = (response: http.ServerResponse): void => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.end('go');
  };
  const server = http.createServer((request, response) => {
    const name = decodeURIComponent(new URL(request.url ?? '/', 'http://localhost').pathname.slice(1));
    const here = point(name);
    here.arrive();
    if (here.released) return answer(response);
    here.held.push(response);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  const release = (name: string): void => {
    const here = point(name);
    here.released = true;
    for (const response of here.held.splice(0)) answer(response);
  };
  return {
    snippet: (name) =>
      [
        'import urllib.request',
        `urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:${port}/${encodeURIComponent(name)}', timeout=300).read()`,
        '',
      ].join('\n'),
    reached: (name) => point(name).arrived,
    release,
    close: async () => {
      for (const name of points.keys()) release(name);
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}
