import * as http from 'node:http';
import type { AddressInfo } from 'node:net';

/** Holds one named claim before it reaches the real hub and records the real response. */
export async function startClaimBarrier(upstreamUrl: string) {
  const upstream = new URL(upstreamUrl);
  let target: string | undefined;
  let arrived: (() => void) | undefined;
  let release: (() => void) | undefined;
  let answered: ((answer: { status: number; contentType: string | undefined; body: unknown }) => void) | undefined;
  let arrival = Promise.resolve();
  let response: Promise<{ status: number; contentType: string | undefined; body: unknown }>;
  const server = http.createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks);
    const matches = req.method === 'POST' && req.url === '/api/fleet/routes' && target !== undefined &&
      (JSON.parse(body.toString()) as { chunk_id?: string }).chunk_id === target;
    if (matches) {
      target = undefined;
      const held = new Promise<void>((resolve) => { release = resolve; });
      arrived?.();
      await held;
    }
    const forwarded = http.request({ hostname: upstream.hostname, port: upstream.port, method: req.method, path: req.url, headers: req.headers }, (reply) => {
      res.writeHead(reply.statusCode!, reply.headers);
      const parts: Buffer[] = [];
      reply.on('data', (part: Buffer) => { if (matches) parts.push(part); });
      reply.on('end', () => { if (matches) answered?.({ status: reply.statusCode!, contentType: reply.headers['content-type'], body: JSON.parse(Buffer.concat(parts).toString()) as unknown }); });
      reply.pipe(res);
    });
    forwarded.on('error', () => res.destroy());
    forwarded.end(body);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    arm(chunkId: string) {
      target = chunkId;
      arrival = new Promise<void>((resolve) => { arrived = resolve; });
      response = new Promise((resolve) => { answered = resolve; });
    },
    arrived: () => arrival,
    release: () => { release?.(); },
    response: () => response,
    async close() {
      release?.();
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}
