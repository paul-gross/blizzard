import * as net from 'node:net';

/** A loopback TCP relay in front of a hub: forwarding, or holding its connections shut so a hub outage stays open. */
export interface HubRelay {
  /** The URL a runner host reaches the hub at. */
  readonly url: string;
  /** Drops the open connections and resets every one that arrives, without dialing the hub, until `release()`; the count starts over. */
  hold(): void;
  /** Forwards connections to the hub again. */
  release(): void;
  /** Connections reset since the last `hold()`. */
  readonly refused: number;
  /** Resolves once a connection has been reset since the last `hold()`: immediately if one already was. */
  refusal(): Promise<void>;
  close(): Promise<void>;
}

/** Starts a relay on a free loopback port forwarding to the hub at `upstreamPort`. */
export async function startHubRelay(upstreamPort: number): Promise<HubRelay> {
  let held = false;
  let refused = 0;
  let waiters: (() => void)[] = [];
  const open = new Set<net.Socket>();

  const track = (socket: net.Socket): void => {
    open.add(socket);
    socket.on('close', () => open.delete(socket));
    socket.on('error', () => socket.destroy());
  };

  const server = net.createServer((client) => {
    track(client);
    if (held) {
      refused += 1;
      client.resetAndDestroy();
      const arrived = waiters;
      waiters = [];
      for (const wake of arrived) wake();
      return;
    }
    const upstream = net.connect(upstreamPort, '127.0.0.1');
    track(upstream);
    client.on('close', () => upstream.destroy());
    upstream.on('close', () => client.destroy());
    client.pipe(upstream);
    upstream.pipe(client);
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address() as net.AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    hold() {
      held = true;
      refused = 0;
      // A hub that is down has dropped its open connections too, so a kept-alive one cannot slip a call through.
      for (const socket of open) socket.destroy();
    },
    release() {
      held = false;
    },
    get refused() {
      return refused;
    },
    refusal: () => (refused > 0 ? Promise.resolve() : new Promise<void>((resolve) => waiters.push(resolve))),
    async close() {
      for (const socket of open) socket.destroy();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}
