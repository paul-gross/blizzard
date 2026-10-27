import * as http from 'node:http';
import { expect, type Frame, runnerTest as test, type RunnerWire } from '../../fixtures';

const RUNNER = 'rn_01CHECK';
const PREFIX = `/runners/${RUNNER}`;

const hub = {
  buffer_depth: 7,
  endpoint: 'http://127.0.0.1:1',
  hub_version: null,
  last_contact_at: null,
  reachable: false,
  window: null,
} satisfies RunnerWire.HubConnectivityView;

const facts = {
  items: [
    { seq: 4, kind: 'lease.minted', chunk_id: 'ch_1', lease_id: 'lease_1', project_id: null, created_at: '2026-01-01T00:00:00Z', acked_at: null },
    { seq: 5, kind: 'runner.locally_paused', chunk_id: null, lease_id: null, project_id: null, created_at: '2026-01-01T00:00:01Z', acked_at: '2026-01-01T00:00:02Z' },
  ],
} satisfies RunnerWire.FactListResponse;

const control = {
  hub_paused: false,
  local_paused: true,
  paused: true,
  runner_id: RUNNER,
  runner_name: 'check',
} satisfies RunnerWire.RunnerControlView;

const floor = { by: 'check', cleared_at: '2026-01-01T00:00:00Z', slug: null } satisfies RunnerWire.FloorClearView;

interface Seen {
  method: string;
  url: string;
  body: string;
}

/** A host stub: the routes the clients call, and an SSE stream the test publishes frames onto. */
async function hostStub(options: { failStatus?: boolean } = {}): Promise<{
  url: string;
  seen: Seen[];
  streamRequests: { url: string; lastEventId: string | undefined }[];
  publish: (event: string, data: unknown) => number;
  dropStreams: () => void;
  stop: () => void;
}> {
  const seen: Seen[] = [];
  const streamRequests: { url: string; lastEventId: string | undefined }[] = [];
  const streams = new Set<http.ServerResponse>();
  let id = 0;
  const server = http.createServer((request, response) => {
    const url = request.url ?? '';
    const route = url.split('?')[0] ?? '';
    if (route === '/api/events/stream') {
      const header = request.headers['last-event-id'];
      streamRequests.push({ url, lastEventId: Array.isArray(header) ? header[0] : header });
      response.writeHead(200, { 'content-type': 'text/event-stream' });
      response.write(': blizzard runner event stream\n\n');
      streams.add(response);
      response.once('close', () => streams.delete(response));
      return;
    }
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => chunks.push(chunk));
    request.once('end', () => {
      seen.push({ method: request.method ?? '', url, body: Buffer.concat(chunks).toString('utf8') });
      const send = (status: number, body: unknown): void => {
        response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
      };
      const get = request.method === 'GET';
      if (get && route === `${PREFIX}/api/runner`) {
        if (options.failStatus === true) return send(500, { detail: 'boom' });
        return send(200, { hub });
      }
      if (get && route === `${PREFIX}/api/facts`) return send(200, facts);
      if (get && route === `${PREFIX}/api/leases`) return send(200, { items: [] } satisfies RunnerWire.LeaseListResponse);
      if (get && route === `${PREFIX}/api/escalations`) return send(200, { items: [] } satisfies RunnerWire.EscalationListResponse);
      if (get && route === '/api/host/runners') return send(200, { items: [] } satisfies RunnerWire.HostRunnerListResponse);
      if (get && route === '/api/host/subscriptions') return send(200, { items: [] } satisfies RunnerWire.HostSubscriptionListResponse);
      if (request.method === 'PATCH' && route === `${PREFIX}/api/runner`) return send(200, control);
      if (request.method === 'POST' && (route === `${PREFIX}/api/runner/floor/clear` || route === '/api/host/subscriptions/claude/floor/clear')) {
        return send(200, floor);
      }
      if (request.method === 'POST' && route.startsWith(`${PREFIX}/api/chunks/`)) return send(202, {});
      return send(404, { detail: 'not found' });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const frame = (event: string, data: unknown, at: number): string => `id: ${at}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  return {
    url: `http://127.0.0.1:${(server.address() as { port: number }).port}`,
    seen,
    streamRequests,
    publish: (event, data) => {
      id += 1;
      for (const stream of streams) stream.write(frame(event, data, id));
      return id;
    },
    dropStreams: () => {
      for (const stream of [...streams]) stream.destroy();
    },
    stop: () => {
      server.closeAllConnections();
      server.close();
    },
  };
}

test('facts come back with seq, kind, chunk_id and acked_at intact', async ({ runnerHost }) => {
  const stub = await hostStub();
  try {
    const runner = runnerHost(stub.url).runner(RUNNER);
    const answer = await runner.facts({ limit: 50 });
    expect(answer.status).toBe(200);
    expect(answer.body).toEqual(facts);
    expect(answer.body.items.map((fact) => [fact.seq, fact.kind, fact.chunk_id, fact.acked_at])).toEqual([
      [4, 'lease.minted', 'ch_1', null],
      [5, 'runner.locally_paused', null, '2026-01-01T00:00:02Z'],
    ]);
    expect(stub.seen[0]?.url).toBe(`${PREFIX}/api/facts?limit=50`);
  } finally {
    stub.stop();
  }
});

test('bufferDepth reads hub.buffer_depth from the status, apart from the facts', async ({ runnerHost }) => {
  const stub = await hostStub();
  try {
    const runner = runnerHost(stub.url).runner(RUNNER);
    expect(await runner.bufferDepth()).toBe(7);
    expect(stub.seen.map((request) => request.url)).toEqual([`${PREFIX}/api/runner`]);
  } finally {
    stub.stop();
  }
});

test('the host, lease, escalation and control calls reach their routes', async ({ runnerHost }) => {
  const stub = await hostStub();
  try {
    const host = runnerHost(stub.url);
    const runner = host.runner(RUNNER);
    await host.runners();
    await host.subscriptions();
    await runner.leases();
    await runner.escalations();
    expect((await runner.setPaused(true, 'check')).body).toEqual(control);
    await runner.pauseChunk('ch_1');
    await runner.resumeChunk('ch_1');
    await runner.requeueChunk('ch_1');
    expect((await runner.clearFloor('check')).body).toEqual(floor);
    expect((await host.clearSubscriptionFloor('claude')).body).toEqual(floor);
    expect((await runner.raw.get('/api/nothing')).status).toBe(404);
    expect(stub.seen.map((request) => `${request.method} ${request.url}`)).toEqual([
      'GET /api/host/runners',
      'GET /api/host/subscriptions',
      `GET ${PREFIX}/api/leases`,
      `GET ${PREFIX}/api/escalations`,
      `PATCH ${PREFIX}/api/runner`,
      `POST ${PREFIX}/api/chunks/ch_1/pause`,
      `POST ${PREFIX}/api/chunks/ch_1/resume`,
      `POST ${PREFIX}/api/chunks/ch_1/requeues`,
      `POST ${PREFIX}/api/runner/floor/clear`,
      'POST /api/host/subscriptions/claude/floor/clear',
      `GET ${PREFIX}/api/nothing`,
    ]);
    expect(JSON.parse(stub.seen[4]?.body ?? '')).toEqual({ paused: true, by: 'check' });
  } finally {
    stub.stop();
  }
});

test('a 5xx fails the call with the host log attached, unless expected', async ({ runnerHost }, testInfo) => {
  const stub = await hostStub({ failStatus: true });
  try {
    const host = runnerHost(stub.url, { logContext: () => 'host log line' });
    const runner = host.runner(RUNNER);
    await expect(runner.status()).rejects.toThrow(new RegExp(`GET .*${PREFIX}/api/runner answered 500, a server error`));
    expect(testInfo.attachments.some((attachment) => attachment.name.startsWith('log around GET'))).toBe(true);
    await expect(runner.bufferDepth()).rejects.toThrow(/answered 500/);
    expect((await runner.expecting(500).status()).status).toBe(500);
    expect((await host.expecting(500).runner(RUNNER).status()).status).toBe(500);
  } finally {
    stub.stop();
  }
});

test('the runner stream resumes on Last-Event-ID and serves no frame twice', async ({ runnerStream }) => {
  const stub = await hostStub();
  try {
    const subscription = await runnerStream(stub.url);
    stub.publish('fact-changed', { n: 1 });
    stub.publish('fact-changed', { n: 2 });
    await subscription.waitForAll([(frame) => frame.data.n === 1, (frame) => frame.data.n === 2]);
    stub.dropStreams();
    await expect.poll(() => stub.streamRequests.length).toBe(2);
    stub.publish('fact-changed', { n: 3 });
    await subscription.waitFor((frame) => frame.data.n === 3);
    expect(stub.streamRequests.map((request) => request.lastEventId)).toEqual([undefined, '2']);
    expect(subscription.frames.map((frame) => frame.data.n)).toEqual([1, 2, 3]);
  } finally {
    stub.stop();
  }
});

test('a runner stream joined from now asks for from=latest', async ({ runnerStream }) => {
  const stub = await hostStub();
  try {
    await runnerStream(stub.url, { fromNow: true });
    expect(stub.streamRequests.map((request) => request.url)).toEqual(['/api/events/stream?from=latest']);
  } finally {
    stub.stop();
  }
});

test('a caller-supplied barrier returns the frames before it and hides its own', async ({ runnerStream }) => {
  const stub = await hostStub();
  try {
    const marks: number[] = [];
    const subscription = await runnerStream(stub.url, {
      barrier: {
        act: async () => {
          marks.push(stub.publish('barrier-mark', { mark: marks.length + 1 }));
        },
        reached: (frame: Frame) => frame.event === 'barrier-mark' && frame.data.mark === marks.length,
        hides: (frame: Frame) => frame.event === 'barrier-mark',
        describe: 'no barrier-mark arrived',
      },
    });
    stub.publish('fact-changed', { n: 1 });
    stub.publish('fact-changed', { n: 2 });
    expect((await subscription.barrier()).map((frame) => frame.data.n)).toEqual([1, 2]);
    stub.publish('fact-changed', { n: 3 });
    expect((await subscription.barrier()).map((frame) => frame.data.n)).toEqual([3]);
    expect(subscription.frames.every((frame) => frame.event !== 'barrier-mark')).toBe(true);
    expect(subscription.frames.map((frame) => frame.data.n)).toEqual([1, 2, 3]);
  } finally {
    stub.stop();
  }
});
